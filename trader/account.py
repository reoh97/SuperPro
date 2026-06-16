"""단일 계정 공유 실행 레이어 — 코어/새틀라이트 두 엔진이 '계정분리 없이' 한 업비트
계정에서 안전하게 병행하기 위한 장부기반(ledger) 브로커 + 정합성 검증.

설계 원칙(왜 계정분리가 필요 없는가):
  - 각 엔진은 '자기 서브장부(state file의 per-coin cash/position)'로만 사이징·청산한다.
  - 실계정 통합잔고(get_balance)는 매매 '판단'에 절대 쓰지 않는다(이중지출·상대코인 매도 방지).
    → LedgerBroker는 주문 '실행'과 '실체결 확인'만 담당. 회계는 엔진 장부가 진실원본.
  - 매도는 항상 '자기가 산 수량(volume)'만 지정 → 업비트 매도는 수량지정이라 상대 코인 불가침.
  - reconcile(): 주기적으로 (엔진들 장부 합) ≈ (실계정 잔고) 인지 대조 → 어긋나면 경보.
    (부분체결·슬리피지 누적·수동입출금·버그로 인한 드리프트를 조기 포착)

⚠️ 실거래는 실제 자산이 오간다. 충분히 모의로 검증한 뒤 소액으로 시작할 것.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


@dataclass
class Fill:
    price: float      # 평균 체결가
    volume: float     # 체결 수량(코인)
    fee: float        # 실제 지불 수수료(원)
    krw: float        # 매수=실제 차감된 원화(수수료 포함) / 매도=체결 총액(수수료 차감 전)
    filled: bool = True   # 지정가 주문 체결 여부(check_order용). 시장가는 항상 True.


class LedgerBroker:
    """실제 업비트 주문 실행 + 실체결 확인. 잔고 조회는 매매판단에 쓰지 않는다.

    한 엔진(프로세스)당 하나씩 생성한다(같은 API키 공유 가능). 엔진별 장부 분리가
    충돌을 막으므로, 이 브로커는 '주문 넣고 → 체결결과 읽어오기'만 한다.
    """

    def __init__(self, access_key: str, secret_key: str, fee: float = 0.0005,
                 poll_tries: int = 8, poll_sec: float = 0.4):
        import os
        import pyupbit
        access = access_key or os.environ.get("UPBIT_ACCESS_KEY", "")
        secret = secret_key or os.environ.get("UPBIT_SECRET_KEY", "")
        if not access or not secret:
            raise RuntimeError(
                "실거래(live)에는 업비트 API 키가 필요합니다. "
                "config.yaml(upbit.access_key/secret_key) 또는 환경변수 "
                "UPBIT_ACCESS_KEY/UPBIT_SECRET_KEY 를 설정하세요. "
                "(출금권한은 절대 켜지 말 것)"
            )
        self.upbit = pyupbit.Upbit(access, secret)
        self.fee = float(fee)
        self.poll_tries = int(poll_tries)
        self.poll_sec = float(poll_sec)

    # ---------- 실체결 조회 ----------
    def _settle(self, resp, fallback_price: float, fallback_krw: float,
                side: str) -> Optional[Fill]:
        """주문 응답(uuid)으로 실제 체결(수량·평단·수수료)을 폴링해 확정."""
        if not resp or not isinstance(resp, dict) or resp.get("error"):
            return None
        uuid = resp.get("uuid")
        if not uuid:
            return None
        order = None
        for _ in range(self.poll_tries):
            try:
                order = self.upbit.get_order(uuid)
            except Exception:
                order = None
            if order and order.get("state") in ("done", "cancel"):
                trades = order.get("trades") or []
                if trades:
                    break
            time.sleep(self.poll_sec)
        # 체결 집계
        if order and order.get("trades"):
            vol = sum(float(t["volume"]) for t in order["trades"])
            funds = sum(float(t["funds"]) for t in order["trades"])   # 체결 총액(수수료 전)
            paid = float(order.get("paid_fee", 0.0) or 0.0)
            avg = funds / vol if vol > 0 else fallback_price
            if side == "buy":
                return Fill(price=avg, volume=vol, fee=paid, krw=funds + paid)
            return Fill(price=avg, volume=vol, fee=paid, krw=funds)
        # 폴링 실패 → 근사 추정(보수적). 정합성 reconcile이 후속 드리프트로 잡아낸다.
        if side == "buy":
            fee = fallback_krw * self.fee
            return Fill(price=fallback_price, volume=(fallback_krw - fee) / fallback_price,
                        fee=fee, krw=fallback_krw)
        gross = fallback_price * fallback_krw   # 매도는 fallback_krw에 volume을 넣어 호출
        return Fill(price=fallback_price, volume=fallback_krw, fee=gross * self.fee, krw=gross)

    def execute_buy(self, ticker: str, krw_amount: float) -> Optional[Fill]:
        """krw_amount(원, 수수료 포함) 시장가 매수 → 실체결 Fill. 자기 서브예산에서만 호출할 것."""
        if krw_amount < 5000:
            return None
        try:
            resp = self.upbit.buy_market_order(ticker, krw_amount)
        except Exception:
            return None
        price = self._safe_price(ticker)
        return self._settle(resp, fallback_price=price, fallback_krw=krw_amount, side="buy")

    def execute_sell(self, ticker: str, volume: float) -> Optional[Fill]:
        """volume(자기가 산 수량) 시장가 매도 → 실체결 Fill. 통합잔고가 아니라 자기 장부 수량만 넘길 것."""
        if volume <= 0:
            return None
        try:
            resp = self.upbit.sell_market_order(ticker, volume)
        except Exception:
            return None
        price = self._safe_price(ticker)
        return self._settle(resp, fallback_price=price, fallback_krw=volume, side="sell")

    def _safe_price(self, ticker: str) -> float:
        import pyupbit
        try:
            p = pyupbit.get_current_price(ticker)
            return float(p) if p else 0.0
        except Exception:
            return 0.0

    # ---------- 지정가(limit) 주문 — V2 슬리피지 회피 ----------
    def place_limit_buy(self, ticker: str, price: float, krw_amount: float) -> Optional[str]:
        """price 지정가 매수 주문(미체결 가능). 체결 시 슬리피지 0. 주문 uuid 반환."""
        if krw_amount < 5000 or price <= 0:
            return None
        volume = krw_amount / price
        try:
            resp = self.upbit.buy_limit_order(ticker, price, volume)
        except Exception:
            return None
        if not resp or not isinstance(resp, dict) or resp.get("error"):
            return None
        return resp.get("uuid")

    def place_limit_sell(self, ticker: str, price: float, volume: float) -> Optional[str]:
        """price 지정가 매도 주문(자기 수량만). 주문 uuid 반환."""
        if volume <= 0 or price <= 0:
            return None
        try:
            resp = self.upbit.sell_limit_order(ticker, price, volume)
        except Exception:
            return None
        if not resp or not isinstance(resp, dict) or resp.get("error"):
            return None
        return resp.get("uuid")

    def check_order(self, uuid: str) -> Optional[Fill]:
        """주문 상태 조회 → 완전체결이면 Fill(filled=True, 실체결), 아니면 filled=False."""
        try:
            order = self.upbit.get_order(uuid)
        except Exception:
            return None
        if not order or not isinstance(order, dict):
            return None
        trades = order.get("trades") or []
        if order.get("state") == "done" and trades:
            vol = sum(float(t["volume"]) for t in trades)
            funds = sum(float(t["funds"]) for t in trades)
            paid = float(order.get("paid_fee", 0.0) or 0.0)
            avg = funds / vol if vol > 0 else 0.0
            krw = funds + paid if order.get("side") == "bid" else funds   # bid=매수
            return Fill(price=avg, volume=vol, fee=paid, krw=krw, filled=True)
        return Fill(price=0.0, volume=0.0, fee=0.0, krw=0.0, filled=False)  # 미체결/부분(완료 전)

    def cancel_order(self, uuid: str) -> bool:
        try:
            self.upbit.cancel_order(uuid)
            return True
        except Exception:
            return False


# ============================ 정합성 검증(reconcile) ============================
def ledger_expectation(state: dict) -> Tuple[float, Dict[str, float]]:
    """엔진 state(JSON 로드) → (보유해야 할 미투입 KRW 합, {currency: 보유수량}).

    두 엔진 모두 호환: position.size + grid[].size 를 합산. cash 합 = 계정에 있어야 할 원화.
    currency 키는 'BTC' 등(티커 'KRW-BTC'에서 추출)."""
    krw = 0.0
    coins: Dict[str, float] = {}
    for tk, c in state.items():
        if not isinstance(c, dict):
            continue
        krw += float(c.get("cash", 0.0) or 0.0)
        cur = tk.split("-")[1] if "-" in tk else tk
        vol = 0.0
        pos = c.get("position")
        if isinstance(pos, dict):
            vol += float(pos.get("size", 0.0) or 0.0)
        for u in (c.get("grid") or []):
            vol += float(u.get("size", 0.0) or 0.0)
        if vol > 0:
            coins[cur] = coins.get(cur, 0.0) + vol
    return krw, coins


def reconcile(ledgers: List[Tuple[str, dict]], upbit,
              tol_krw: float = 10000.0, tol_pct: float = 0.01) -> dict:
    """엔진 장부 합 vs 실계정 잔고 대조.

    ledgers: [(엔진이름, state_dict), ...]
    upbit:   pyupbit.Upbit 인스턴스(잔고 조회 전용 — 매매판단 아님)
    반환: {ok: bool, krw: {...}, coins: {cur: {...}}, alerts: [..]}
    """
    exp_krw = 0.0
    exp_coins: Dict[str, float] = {}
    for _, st in ledgers:
        k, cs = ledger_expectation(st)
        exp_krw += k
        for cur, v in cs.items():
            exp_coins[cur] = exp_coins.get(cur, 0.0) + v

    # 실계정 잔고
    real: Dict[str, float] = {}
    try:
        for b in (upbit.get_balances() or []):
            real[b["currency"]] = float(b.get("balance", 0.0) or 0.0) \
                + float(b.get("locked", 0.0) or 0.0)
    except Exception as e:
        return {"ok": False, "alerts": [f"잔고 조회 실패: {e}"], "krw": {}, "coins": {}}

    alerts: List[str] = []
    real_krw = real.get("KRW", 0.0)
    krw_drift = real_krw - exp_krw
    krw_info = {"expected": exp_krw, "real": real_krw, "drift": krw_drift}
    # 실계정 KRW가 장부보다 '부족'하면 이중지출 위험 → 경보(여유분 초과는 미투입 자금이라 허용)
    if real_krw + tol_krw < exp_krw:
        alerts.append(f"⚠️ KRW 부족: 장부 {exp_krw:,.0f} > 실계정 {real_krw:,.0f} "
                      f"(부족 {exp_krw-real_krw:,.0f}원) — 이중지출/외부인출 의심")

    coins_info = {}
    all_curs = set(exp_coins) | {c for c in real if c != "KRW"}
    for cur in sorted(all_curs):
        e = exp_coins.get(cur, 0.0)
        r = real.get(cur, 0.0)
        coins_info[cur] = {"expected": e, "real": r, "drift": r - e}
        if e > 0 and abs(r - e) > e * tol_pct:
            alerts.append(f"⚠️ {cur} 수량 불일치: 장부 {e:.8f} vs 실계정 {r:.8f} "
                          f"(드리프트 {(r-e):+.8f}) — 부분체결/슬리피지/상대코인매도 의심")
        elif e == 0 and r > 0:
            alerts.append(f"ℹ️ {cur}: 장부엔 없는 잔고 {r:.8f} 존재(수동보유/이전잔고?)")

    return {"ok": len(alerts) == 0, "krw": krw_info, "coins": coins_info, "alerts": alerts}
