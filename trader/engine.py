"""매매 엔진: 백그라운드 스레드에서 전략을 주기적으로 평가하고 주문을 실행.

흐름:
  1) 일봉/현재가 조회 → 변동성 돌파 목표가 계산
  2) 날짜(일봉)가 바뀌면 보유 포지션을 시가 청산하고 매수 가능 상태로 리셋
  3) 미보유 + 매수신호면 매수
  4) interval_sec 마다 반복

대시보드가 읽어갈 수 있도록 매 루프 상태를 snapshot()으로 노출합니다.
"""
from __future__ import annotations

import threading
import time
import traceback
from datetime import datetime
from typing import Any, Dict, Optional

from . import data, strategy
from .ai_advisor import AIAdvisor, AdvisorResult
from .broker import Broker
from .news import NewsFeed
from .state import StateStore


class Engine:
    def __init__(self, config: dict, store: StateStore, broker: Broker,
                 advisor: Optional[AIAdvisor] = None,
                 news: Optional[NewsFeed] = None):
        self.cfg = config
        self.store = store
        self.broker = broker
        self.advisor = advisor
        self.news = news
        self.ticker = config["ticker"]

        self._thread: Optional[threading.Thread] = None
        self._running = threading.Event()      # 매매 활성화 여부
        self._stop = threading.Event()         # 스레드 종료 신호

        # 대시보드 노출용 최신 상태
        self._lock = threading.Lock()
        self._last_signal: Optional[strategy.Signal] = None
        self._current_price: Optional[float] = None
        self._last_error: Optional[str] = None
        self._last_update: Optional[str] = None
        self._bought_date: Optional[str] = None   # 이번 봉에서 이미 매수했는지
        self._position_date: Optional[str] = None  # 현재 포지션을 잡은 봉 날짜

        # AI 판단 상태
        self._ai_result: Optional[AdvisorResult] = None
        self._ai_at: Optional[str] = None
        self._last_ai_ts: float = 0.0

    # ----- 스레드 제어 -----
    def start_loop(self):
        """백그라운드 스레드 기동(아직 매매는 비활성)."""
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def enable(self):
        self._running.set()

    def disable(self):
        self._running.clear()

    def shutdown(self):
        self._stop.set()

    def is_enabled(self) -> bool:
        return self._running.is_set()

    # ----- 메인 루프 -----
    def _run(self):
        interval = float(self.cfg["loop"]["interval_sec"])
        while not self._stop.is_set():
            try:
                self._tick()
                self._last_error = None
            except Exception:
                self._last_error = traceback.format_exc(limit=3)
            # interval 동안 잘게 끊어 종료 신호에 빠르게 반응
            for _ in range(int(max(interval, 1))):
                if self._stop.is_set():
                    break
                time.sleep(1)

    def _tick(self):
        ma_period = int(self.cfg["strategy"]["ma_period"])
        df = data.daily_ohlcv(self.ticker, count=max(ma_period + 2, 5))
        price = data.current_price(self.ticker)
        if df is None or price is None:
            raise RuntimeError("시세 조회 실패 (네트워크/티커 확인)")

        sig = strategy.evaluate(
            df, price,
            k=float(self.cfg["strategy"]["k"]),
            ma_period=ma_period,
        )

        with self._lock:
            self._current_price = price
            self._last_signal = sig
            self._last_update = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if sig is None:
            return

        # 매매가 비활성이면 신호만 갱신하고 주문은 하지 않음
        if not self._running.is_set():
            return

        today = sig.candle_date

        # 1) 날짜 변경 → 보유분 청산(다음 날 시가 매도)
        if self.store.portfolio.has_position() and self._position_date is not None \
                and self._position_date != today:
            self.broker.sell(price, self.store.portfolio.volume)
            self._position_date = None

        # 2) 매수 후보: 이번 봉에서 아직 안 샀고, 미보유, 차트 매수신호일 때
        if (not self.store.portfolio.has_position()
                and self._bought_date != today
                and sig.should_buy):
            # 2-a) AI 종합 판단 (활성화된 경우). 차트 신호가 났을 때만, 쿨다운 두고 호출.
            if not self._ai_gate(sig, price):
                return  # AI가 보류(HOLD)했거나 쿨다운 중 → 이번엔 매수 안 함

            ratio = float(self.cfg["trade"]["invest_ratio"])
            min_krw = float(self.cfg["trade"]["min_order_krw"])
            krw = self.broker.krw_balance() * ratio
            if krw >= min_krw:
                fill = self.broker.buy(price, krw)
                if fill:
                    self._bought_date = today
                    self._position_date = today

    def _ai_gate(self, sig, price: float) -> bool:
        """AI 뉴스 판단으로 매수를 최종 승인할지 결정.

        - AI 비활성/키 없음 → 차트 신호만으로 매수 승인(True)
        - 쿨다운(min_interval_sec) 중 → 직전 판단 재사용
        - BUY + 확신도 충족 → True, 그 외 → False
        """
        if self.advisor is None or not self.advisor.enabled:
            return True  # AI 미사용 시 차트 신호를 그대로 따름

        now = time.time()
        interval = float(self.cfg.get("ai", {}).get("min_interval_sec", 900))
        # 쿨다운 중이면 마지막 판단을 재사용 (비용 제어)
        if self._ai_result is not None and (now - self._last_ai_ts) < interval:
            return self.advisor.approves_buy(self._ai_result)

        headlines = self.news.latest() if self.news else []
        result = self.advisor.decide(self.ticker, sig, price, headlines)
        with self._lock:
            self._ai_result = result
            self._ai_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._last_ai_ts = now
        return self.advisor.approves_buy(result)

    # ----- 대시보드용 스냅샷 -----
    def snapshot(self) -> Dict[str, Any]:
        p = self.store.portfolio
        price = self._current_price
        sig = self._last_signal

        coin_value = (price or 0.0) * p.volume
        total_equity = p.krw + coin_value
        unrealized = (price - p.avg_price) * p.volume if (price and p.has_position()) else 0.0

        return {
            "running": self._running.is_set(),
            "mode": self.cfg["mode"],
            "ticker": self.ticker,
            "current_price": price,
            "last_update": self._last_update,
            "last_error": self._last_error,
            "signal": {
                "candle_date": sig.candle_date,
                "target_price": sig.target_price,
                "today_open": sig.today_open,
                "range_value": sig.range_value,
                "ma": sig.ma,
                "ma_ok": sig.ma_ok,
                "breakout": sig.breakout,
                "should_buy": sig.should_buy,
            } if sig else None,
            "portfolio": {
                "krw": p.krw,
                "volume": p.volume,
                "avg_price": p.avg_price,
                "coin_value": coin_value,
                "total_equity": total_equity,
                "realized_pnl": p.realized_pnl,
                "unrealized_pnl": unrealized,
                "has_position": p.has_position(),
            },
            "trades": [
                {
                    "time": t.time, "side": t.side, "price": t.price,
                    "volume": t.volume, "amount": t.amount, "pnl": t.pnl,
                }
                for t in reversed(p.trades[-30:])
            ],
            "ai": {
                "enabled": bool(self.advisor and self.advisor.enabled),
                "model": self.advisor.model if self.advisor else None,
                "disabled_reason": self.advisor.disabled_reason if self.advisor else None,
                "decided_at": self._ai_at,
                "action": self._ai_result.action if self._ai_result else None,
                "confidence": self._ai_result.confidence if self._ai_result else None,
                "reason": self._ai_result.reason if self._ai_result else None,
            },
        }
