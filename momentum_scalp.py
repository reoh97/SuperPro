"""P2-모멘텀: '순간 폭등 올라타기' 스캘퍼 — 검증 우선.

추세 무관. 강한 매수세 + 순간 급등(거래량 폭발 + 단기 급등) 중인 코인을 잡아타,
2~3% 익절 / 작은 손절로 빠진다. 폐기된 P3와의 차이: **익절 목표가 큼(2~3%)** → 수수료 무시 가능.
진짜 검증 포인트: 급등 감지 후 '계속 가나(연속성)' vs '꼭대기 매수(반전)'.

진입(미보유): 최근 K봉 수익 ≥ surge + 거래량 ≥ vol_ma×mult (+ 직전봉 양봉).
청산: 익절(tp 2~3%) | 손절(sl) | 타임아웃. 봉내 보수적(SL 먼저).
사용법: python momentum_scalp.py
"""
from __future__ import annotations
import os, sys, numpy as np, yaml
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import circuit_backtest as cb
from trader import indicators

try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass

FEE = 0.0005


def run_coin(e, p):
    K, surge, vmult, tp, sl, hold = p["K"], p["surge"], p["vmult"], p["tp"], p["sl"], p["hold"]
    cap = 1_000_000.0; eq = cap; pos = None; pnls = []
    c = e["close"].to_numpy(float); h = e["high"].to_numpy(float)
    lo = e["low"].to_numpy(float); o = e["open"].to_numpy(float)
    vol = e["volume"].to_numpy(float); vma = e["vol_ma"].to_numpy(float)
    n = len(c)
    for i in range(210, n):
        if pos is not None:
            pos["held"] += 1
            ex = None
            if lo[i] <= pos["sl_p"]: ex = pos["sl_p"]
            elif h[i] >= pos["tp_p"]: ex = pos["tp_p"]
            elif pos["held"] >= hold: ex = c[i]
            if ex is not None:
                proceeds = ex*pos["sz"]*(1-FEE); pnls.append(proceeds-pos["cost"]); eq += proceeds; pos = None
            continue
        # 급등 감지: 최근 K봉 수익 + 거래량 폭발 + 직전봉 양봉(매수세)
        if i-K < 0: continue
        ret_k = c[i]/c[i-K]-1
        if ret_k >= surge and vol[i] >= vma[i]*vmult and c[i] > o[i]:
            spend = eq; px = c[i]; fee = spend*FEE
            sz = (spend-fee)/px; eq -= spend
            pos = {"sz": sz, "cost": spend, "tp_p": px*(1+tp), "sl_p": px*(1-sl), "held": 0}
    if pos is not None:
        proceeds = c[-1]*pos["sz"]*(1-FEE); pnls.append(proceeds-pos["cost"]); eq += proceeds
    return (eq/cap-1)*100, pnls


def evaluate(data, p):
    rets = []; allp = []; ns = []; days = None
    for tk, df in data.items():
        e = indicators.enrich(df, cfg)
        days = (df.index[-1]-df.index[0]).total_seconds()/86400
        ret, pnls = run_coin(e, p); rets.append(ret); allp += pnls; ns.append(len(pnls))
    w = [x for x in allp if x > 0]; l = [x for x in allp if x < 0]
    wr = len(w)/len(allp)*100 if allp else 0
    pf = sum(w)/-sum(l) if l else 9.9
    return np.mean(rets), wr, pf, (np.mean(ns)/days if days else 0), len(allp)


cfg = yaml.safe_load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml"), encoding="utf-8"))
datasets = {k: cb.load_csv(k, cfg) for k in ("bull", "sideways", "bear")}

print("="*74)
print("  P2 '순간 폭등 올라타기' 스캘퍼 — 실 KRW 검증 (수수료 왕복 0.1% 차감)")
print("  진입: 최근 급등 + 거래량 폭발 + 양봉 / 청산: 2~3% 익절 or 손절")
print("="*74)
grid = [
    {"K": 3, "surge": 0.03, "vmult": 2.0, "tp": 0.025, "sl": 0.015, "hold": 12},
    {"K": 4, "surge": 0.04, "vmult": 2.0, "tp": 0.030, "sl": 0.020, "hold": 16},
    {"K": 2, "surge": 0.03, "vmult": 2.5, "tp": 0.020, "sl": 0.012, "hold": 8},
    {"K": 3, "surge": 0.05, "vmult": 3.0, "tp": 0.030, "sl": 0.020, "hold": 16},
    {"K": 4, "surge": 0.04, "vmult": 1.5, "tp": 0.025, "sl": 0.015, "hold": 12},
]
for p in grid:
    print(f"\n  ── K{p['K']} 급등≥{p['surge']*100:.0f}% 거래량×{p['vmult']} tp{p['tp']*100:.0f}%/sl{p['sl']*100:.0f}% ──")
    print(f"  {'국면':<10}{'순익':>9}{'승률':>7}{'PF':>6}{'거래/일':>8}{'총거래':>7}")
    for k, label in (("bull","불장"),("sideways","횡보"),("bear","약세")):
        ret, wr, pf, tpd, nt = evaluate(datasets[k], p)
        print(f"  {label:<10}{ret:>+8.2f}%{wr:>6.0f}%{pf:>6.2f}{tpd:>8.2f}{nt:>7}")
print("\n  판정: 급등 후 '연속'이면 승률·PF↑(가능성), '꼭대기매수'면 승률<40%+적자(P3 재현).")
