"""모의 관찰: 라이브 엔진을 과거 데이터로 '봉마다 재생'하며 공격모드 전환을 확인.

실시간 모의(run.py)는 업비트 시세가 필요하다. 거래소가 막힌 환경에선 대신 이 도구로
**라이브 엔진(LiveTrader._eval_coin)의 실제 결정 경로**를 과거 불장 데이터에 흘려보내며,
AI 뷰를 BULL로 주입했을 때 'AI 불장 공격모드'가 실제로 켜지고 행동이 바뀌는지 관찰한다.

  - 보수(AI 없음/BULL 아님): require_adx_rising ON, trail 0.025, DOWN플립에 청산.
  - 공격(AI=BULL 확신):       require_adx_rising OFF, trail 0.04, DOWN플립에 홀드.

데이터: backtest_data/bull__<티커>.csv (있으면, 라이브 동일 KRW) → 없으면 BTC/USD 프록시(data/btcusd_1min.csv.gz).
사용법:
    python paper_replay.py
⚠️ 프록시는 BTC/USD 단일·KRW파라미터 → 절대수치는 실전과 다름. 메커니즘 확인용.
정확한 모의는 업비트 접근 가능한 곳에서 run.py 를 paper 모드로 띄울 것.
"""
from __future__ import annotations

import os
import sys
import tempfile

import pandas as pd
import yaml

BASE = os.path.dirname(os.path.abspath(__file__))
GZ = os.path.join(BASE, "data", "btcusd_1min.csv.gz")

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


class _AIView:
    def __init__(self, regime, risk_off=False, confidence=80):
        self.regime, self.risk_off, self.confidence = regime, risk_off, confidence
        self.reason = "(주입된 관찰용 뷰)"


def _proxy_bull() -> pd.DataFrame:
    df = pd.read_csv(GZ)
    df["dt"] = pd.to_datetime(df["timestamp"], unit="s")
    df = df.set_index("dt").sort_index()
    m15 = pd.DataFrame({
        "open": df["open"].resample("15min").first(), "high": df["high"].resample("15min").max(),
        "low": df["low"].resample("15min").min(), "close": df["close"].resample("15min").last(),
        "volume": df["volume"].resample("15min").sum()}).dropna()
    return m15.loc["2024-02-08":"2024-03-05"]


def replay(cfg, bars, ai_view, label, budget):
    # 라이브 엔진이 호출하는 데이터/시세 함수를 재생용으로 교체
    from trader import live as livemod, backtest as btmod
    import pyupbit
    state = {"df": None, "price": None}
    btmod.fetch_minutes = lambda ticker, unit, count, to_kst=None: state["df"]
    pyupbit.get_current_price = lambda ticker: state["price"]

    lt = livemod.LiveTrader(cfg, advisor=None, news=None, state_path=tempfile.mktemp(suffix=".json"))
    lt.enable()
    tk = cfg["portfolio"]["tickers"][0]
    on = 0
    for i in range(210, len(bars)):
        state["df"] = bars.iloc[i - 199:i + 1]       # 라이브와 동일: 직전 200봉
        state["price"] = float(bars["close"].iloc[i])
        lt._ai_view = ai_view                          # 실시간 AI 대체(주입)
        if lt._bull_tier() is not None:
            on += 1
        lt._eval_coin(tk)
    c = lt.coins[tk]
    tr = c["trades"]
    buys = sum(1 for t in tr if t["side"] == "buy")
    sells = sum(1 for t in tr if t["side"] == "sell")
    sr = {}
    for t in tr:
        if t["side"] == "sell":
            sr[t["reason"]] = sr.get(t["reason"], 0) + 1
    pos = c["position"]
    eq = c["cash"] + (pos["size"] * state["price"] if pos else 0.0)
    print(f"\n[{label}]  공격모드 활성 {on}/{len(bars)-210} 스텝")
    print(f"  매수 {buys} · 매도 {sells} · 청산사유 {sr}")
    print(f"  최종 평가액 {eq:,.0f}  수익률 {(eq/budget-1)*100:+.2f}%")
    return eq


def main():
    cfg = yaml.safe_load(open(os.path.join(BASE, "config.yaml"), encoding="utf-8"))
    # backtest_data 불장 CSV가 있으면 첫 종목 사용(라이브 동일 KRW), 없으면 BTC 프록시
    data_dir = os.path.join(BASE, "backtest_data")
    csvs = [f for f in os.listdir(data_dir)] if os.path.isdir(data_dir) else []
    bull_csv = next((f for f in csvs if f.startswith("bull__") and f.endswith(".csv")), None)
    if bull_csv:
        tk = bull_csv[len("bull__"):-len(".csv")]
        bars = pd.read_csv(os.path.join(data_dir, bull_csv), index_col=0, parse_dates=True)
        src = f"{tk} (backtest_data, KRW 실데이터)"
    else:
        tk = "KRW-BTC"
        bars = _proxy_bull()
        src = "BTC/USD 프록시"
    cfg["portfolio"]["tickers"] = [tk]
    cfg["portfolio"]["per_coin_krw"] = 20_000_000
    budget = 20_000_000
    chg = (bars["close"].iloc[-1] / bars["close"].iloc[0] - 1) * 100
    print("=" * 64)
    print(f"  모의 재생: 라이브 엔진 공격모드 관찰  ({src})")
    print(f"  불장 {len(bars)}봉, 가격 {chg:+.1f}%")
    print("=" * 64)

    replay(cfg, bars, None, "보수(AI 없음 → bull_mode OFF)", budget)
    replay(cfg, bars, _AIView("BULL", confidence=80), "공격(AI=BULL 확신 → bull_mode ON)", budget)
    print("\n  · AI=BULL 주입 시 엔진이 공격모드로 전환(adx완화·trail넓게·DOWN홀드)되는지 확인용.")
    if not bull_csv:
        print("  ⚠️ BTC/USD 프록시 — 절대수치는 실전과 다름. KRW는 fetch_local 후 재실행.")


if __name__ == "__main__":
    main()
