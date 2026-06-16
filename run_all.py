"""통합 실행기 — 새틀라이트(단기)+코어(중장기) 두 엔진을 한 프로세스에서 병행 구동하고
좌/우 2단 통합 대시보드를 띄운다.

  좌측: 🛰 새틀라이트(LiveTrader)  ·  우측: 🪨 코어(LongTrendTrader)
  둘 다 같은 업비트 계정 공유(장부 분리). 각 패널에서 개별 시작/정지.

사용법:
    python run_all.py            # config.yaml 사용
  브라우저: http://127.0.0.1:8000   (매매는 각 패널 [시작] 버튼으로 활성화 — 안전 기본 정지)
  ⚠️ mode: live 면 실주문이 나간다. 충분히 모의 검증 후 소액으로. 출금권한 금지.
"""
from __future__ import annotations

import os
import sys
import threading
import time
import traceback

import uvicorn
import yaml

from trader.ai_advisor import AIAdvisor
from trader.live import LiveTrader
from trader.longtrend import LongTrendTrader
from trader.news import NewsFeed
from trader.safety import Notifier, RiskGuard
from web.app import create_combined_app

BASE = os.path.dirname(os.path.abspath(__file__))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def load_dotenv(path: str) -> None:
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val


def _start_monitor(satellite, core, guard, notifier, cfg, mode):
    """감시 스레드: 합산자산→차단기, 새 체결·에러 알림, 생존신호(heartbeat)."""
    poll = max(int(cfg.get("safety", {}).get("poll_sec", 30)), 10)
    hb_min = float(cfg.get("safety", {}).get("heartbeat_min", 60))
    state = {"seen_trade": "", "last_err": "", "last_hb": 0.0}

    def equity_of():
        s = satellite.snapshot(); c = core.status()
        return (s["total"]["equity"], c["total_equity"],
                s["total"]["equity"] + c["total_equity"], s, c)

    def collect_trades(snap_sat, snap_core):
        rows = []
        for c in snap_sat.get("coins", []):
            for t in (c.get("recent_trades") or []):
                rows.append((t.get("time", ""), c["ticker"], t))
        for c in snap_core.get("coins", []):
            for t in (c.get("recent_trades") or []):
                rows.append((t.get("time", ""), c["ticker"], t))
        return sorted(rows)

    def loop():
        notifier.send(f"🤖 <b>SuperPro 가동</b> ({'실거래' if mode=='live' else '모의'}) — "
                      f"코어+새틀 감시 시작. 차단기 낙폭 -{guard.max_dd*100:.0f}%/일일 -{guard.max_daily*100:.0f}%")
        while True:
            try:
                eq_s, eq_c, eq_tot, snap_s, snap_c = equity_of()
                # 1) 차단기
                halted = guard.update(eq_tot)
                satellite.set_halt(halted); core.set_halt(halted)
                # 2) 새 체결 알림
                if notifier.notify_trades:
                    for tm, tk, t in collect_trades(snap_s, snap_c):
                        if tm > state["seen_trade"]:
                            state["seen_trade"] = tm
                            side = "🟦매수" if t.get("side") == "buy" else "🟧매도"
                            pnl = t.get("pnl")
                            extra = (f" 손익 {pnl:+,.0f}원" if pnl is not None else "")
                            notifier.send(f"{side} {tk.replace('KRW-','')} @ {t.get('price'):,.0f}"
                                          f"{extra}  <i>{t.get('reason','')}</i>")
                # 3) 에러 알림
                if notifier.notify_errors:
                    err = snap_s.get("last_error") or snap_c.get("error") or ""
                    if err and err != state["last_err"]:
                        state["last_err"] = err
                        notifier.send(f"⚠️ <b>엔진 오류</b>\n{err.splitlines()[-1][:200]}")
                # 4) 생존신호
                now = time.time()
                if hb_min > 0 and now - state["last_hb"] >= hb_min * 60:
                    state["last_hb"] = now
                    tag = "🔴차단중" if halted else "🟢정상"
                    notifier.send(f"💓 {tag} 합산 {eq_tot:,.0f}원 "
                                  f"(새틀 {eq_s:,.0f} / 코어 {eq_c:,.0f})")
            except Exception:
                if notifier.notify_errors:
                    notifier.send(f"⚠️ 감시 스레드 예외\n{traceback.format_exc(limit=2)[:200]}")
            time.sleep(poll)

    threading.Thread(target=loop, daemon=True).start()


def main():
    load_dotenv(os.path.join(BASE, ".env"))
    cfg_name = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    cfg_path = cfg_name if os.path.isabs(cfg_name) else os.path.join(BASE, cfg_name)
    cfg = yaml.safe_load(open(cfg_path, encoding="utf-8"))
    mode = cfg.get("mode", "paper")

    # 실거래: 한 계정 공유 → 엔진별 장부기반 브로커(자기 서브예산/수량만). 모의면 None.
    sat_broker = core_broker = None
    if mode == "live":
        from trader.account import LedgerBroker
        up = cfg.get("upbit", {})
        fee = float(cfg.get("trade", {}).get("fee", 0.0005))
        sat_broker = LedgerBroker(up.get("access_key", ""), up.get("secret_key", ""), fee=fee)
        core_broker = LedgerBroker(up.get("access_key", ""), up.get("secret_key", ""), fee=fee)

    # 새틀라이트(단기 전술 + AI 게이트)
    ai_cfg = cfg.get("ai", {})
    advisor = AIAdvisor(cfg)
    news = NewsFeed(feeds=cfg.get("news_feeds", []),
                    cache_sec=int(ai_cfg.get("news_cache_sec", 900)),
                    max_items=int(ai_cfg.get("news_max_items", 12)))
    satellite = LiveTrader(cfg, advisor=advisor, news=news,
                           state_path=os.path.join(BASE, "data", f"live_{mode}.json"),
                           broker=sat_broker)

    # 코어(중장기 추세추종)
    core_enabled = cfg.get("longterm", {}).get("enabled", False)
    core = LongTrendTrader(cfg, state_path=os.path.join(BASE, "data", f"longterm_{mode}.json"),
                           broker=core_broker)

    satellite.start_loop()
    core.start_loop()       # 둘 다 평가 루프 기동(매매는 대시보드에서 활성화)

    # ── 운영 안전망: 차단기 + 텔레그램 알림 감시 스레드 ──
    notifier = Notifier(cfg)
    guard = RiskGuard(cfg, state_path=os.path.join(BASE, "data", f"riskguard_{mode}.json"),
                      notifier=notifier)
    _start_monitor(satellite, core, guard, notifier, cfg, mode)

    app = create_combined_app(satellite, core)
    print("=" * 64)
    print(f"  SuperPro 통합 대시보드  ({'실거래' if mode=='live' else '모의'} 모드)")
    core_total = sum(core._per_coin(tk) for tk in core.tickers)
    print(f"  🛰 새틀 {satellite.per_coin:,.0f}원×{len(satellite.tickers)}알트={satellite.per_coin*len(satellite.tickers):,.0f}  "
          f"|  🪨 코어 {core.per_coin:,.0f}원×{len(core.tickers)}메이저={core_total:,.0f}"
          f"{'' if core_enabled else '  (longterm.enabled=false — 코어 매매 비활성)'}")
    if mode == "live":
        print("  ⚠️ 실거래: 같은 계정 공유. reconcile.py 로 정합성 점검 권장. 출금권한 금지.")
    print("  http://127.0.0.1:8000   (매매는 각 패널 [시작] 버튼)")
    print("=" * 64)
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")


if __name__ == "__main__":
    main()
