"""P2-스캘프: 평균회귀 빽빽 스캘퍼 — 검증 우선.

폐기된 P3는 '돌파 추격=꼭대기 매수'로 망함. 정반대로 **저점 매수·작은 반등 익절**(평균회귀)을
빽빽하게 돌려, 왕복 수수료 0.1%를 넘는 조합이 실 KRW 데이터에 존재하는지부터 검증한다.

진입(미보유): 볼린저 하단 이탈 + RSI 과매도 (단, 강한 하락추세=낙하칼날 회피).
청산: 작은 익절(tp, 수수료 위) | 손절(sl) | 짧은 타임아웃. 봉내 보수적(SL 먼저).
사용법: python scalp_backtest.py
"""
from __future__ import annotations
import os, sys, numpy as np, yaml
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import circuit_backtest as cb
from trader import indicators

try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass

FEE = 0.0005  # 편도(왕복 0.1%)


def scalp_run(e, p):
    """평균회귀 스캘프 1종목. p=파라미터. 반환 (수익률%, 승률, 거래수, pnl리스트)."""
    rsi_buy, tp, sl, hold = p["rsi_buy"], p["tp"], p["sl"], p["hold"]
    cap = 1_000_000.0; eq = cap; pos = None; pnls = []
    rows = e.reset_index(); tcol = rows.columns[0]
    warm = 210
    for i in range(warm, len(rows)):
        r = rows.iloc[i]
        if pos is not None:
            pos["held"] += 1
            ex = None
            if r["low"] <= pos["sl_p"]:          # 손절 먼저(보수)
                ex = pos["sl_p"], "sl"
            elif r["high"] >= pos["tp_p"]:
                ex = pos["tp_p"], "tp"
            elif pos["held"] >= hold:
                ex = r["close"], "to"
            if ex:
                px = ex[0]; proceeds = px*pos["sz"]*(1-FEE)
                net = proceeds - pos["cost"]; eq += proceeds; pnls.append(net); pos = None
            continue
        # 진입: 볼린저 하단 이탈 + 과매도. 강한 하락추세(가격<느린EMA & ADX높음 & -DI>+DI) 회피.
        knife = (r["close"] < r["ema_slow"]) and (r["adx"] > 25) and (r["minus_di"] > r["plus_di"])
        if (r["close"] <= r["bb_low"]) and (r["rsi"] <= rsi_buy) and not knife:
            spend = eq; px = float(r["close"]); fee = spend*FEE
            sz = (spend-fee)/px; eq -= spend
            pos = {"sz": sz, "cost": spend, "tp_p": px*(1+tp), "sl_p": px*(1-sl), "held": 0}
    if pos is not None:
        px = float(rows.iloc[-1]["close"]); proceeds = px*pos["sz"]*(1-FEE)
        pnls.append(proceeds-pos["cost"]); eq += proceeds
    return (eq/cap-1)*100, pnls


def evaluate(label, data, p):
    rets = []; allp = []; ns = []; days = None
    for tk, df in data.items():
        e = indicators.enrich(df, cfg)
        days = (df.index[-1]-df.index[0]).total_seconds()/86400
        ret, pnls = scalp_run(e, p)
        rets.append(ret); allp += pnls; ns.append(len(pnls))
    w = [x for x in allp if x > 0]; l = [x for x in allp if x < 0]
    wr = len(w)/len(allp)*100 if allp else 0
    pf = sum(w)/-sum(l) if l else 9.9
    tpd = np.mean(ns)/days if days else 0
    return np.mean(rets), wr, pf, tpd


cfg = yaml.safe_load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml"), encoding="utf-8"))
datasets = {k: cb.load_csv(k, cfg) for k in ("bull", "sideways", "bear")}

print("="*72)
print("  P2 평균회귀 빽빽 스캘퍼 — 실 KRW 검증 (수수료 왕복 0.1% 차감)")
print("="*72)
# 파라미터 그리드: tp(익절)·sl(손절)·rsi(진입 과매도)·hold(타임아웃)
grid = [
    {"rsi_buy": 35, "tp": 0.005, "sl": 0.008, "hold": 12},
    {"rsi_buy": 35, "tp": 0.007, "sl": 0.010, "hold": 16},
    {"rsi_buy": 30, "tp": 0.006, "sl": 0.010, "hold": 16},
    {"rsi_buy": 40, "tp": 0.004, "sl": 0.008, "hold": 8},   # 더 빽빽(완화·작은익절)
    {"rsi_buy": 30, "tp": 0.010, "sl": 0.012, "hold": 24},  # 덜 빽빽(큰익절)
]
for p in grid:
    print(f"\n  ── tp{p['tp']*100:.1f}% sl{p['sl']*100:.1f}% rsi<{p['rsi_buy']} hold{p['hold']} ──")
    print(f"  {'국면':<10}{'순익':>9}{'승률':>7}{'PF':>6}{'거래/일':>9}")
    for k, label in (("bull","불장"),("sideways","횡보"),("bear","약세")):
        ret, wr, pf, tpd = evaluate(label, datasets[k], p)
        print(f"  {label:<10}{ret:>+8.2f}%{wr:>6.0f}%{pf:>6.2f}{tpd:>9.2f}")
print("\n  판정: 수수료 넘고 +면 가능성, 전부 -면 P3처럼 폐기. 횡보가 평균회귀의 본진.")
