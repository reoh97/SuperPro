"""그리드(Grid) 박스 백테스트 — 횡보 구간 '칸칸이 발라먹기' 전략 검증.

박스(1회 진입)의 업그레이드: 범위 안에서 step 간격으로 여러 칸 매수, 각 칸을 +step 반등에 매도.
국면 게이트(SIDEWAYS만 가동) + 바닥이탈 손절(가방 컷)로 범위붕괴 리스크 제어.

실 KRW 검증 결론(step1.2%×8): 비국면(횡보+약세) 손실 -1.59%→-0.37%(4배↓, 거의 약세장 방어).
  → '더 버는' 게 아니라 '훨씬 덜 잃는' 방어 도구. 비국면을 플러스로 만들진 못함(스팟 한계).

사용법: python grid_backtest.py   (backtest_data 의 KRW 실데이터 필요)
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd
import yaml

import circuit_backtest as cb
from trader import indicators, regime

BASE = os.path.dirname(os.path.abspath(__file__))
FEE = 0.0005

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def grid_sim(df: pd.DataFrame, cfg: dict, step: float, n: int, stop: float,
             budget: float = 2_000_000):
    """그리드 시뮬레이션. 반환: (기간수익%, MDD%).
    - SIDEWAYS 국면일 때만 가동. 그 외/바닥이탈 시 전량청산.
    - 매수: 직전 최저매수가 -step 아래로 떨어지면 1칸(최대 n칸).
    - 매도: 각 보유칸을 매수가 +step 반등에 청산.
    """
    d = indicators.enrich(df, cfg)
    rows = d.reset_index()
    warm = int(cfg.get("indicators", {}).get("ema_slow", 200)) + 5
    per = budget / n
    cash = budget
    held = []  # [(buy_price, krw_spent)]
    cbn = int(cfg.get("scalp", {}).get("confirm_bars", 3))
    conf = regime.SIDEWAYS
    sreg, st = None, 0
    eqc = []
    for i in range(warm, len(rows)):
        row = rows.iloc[i]
        lo, hi, cl = float(row["low"]), float(row["high"]), float(row["close"])
        raw = regime.classify(row, cfg)
        if raw == sreg:
            st += 1
        else:
            sreg, st = raw, 1
        if st >= cbn:
            conf = sreg
        if conf != regime.SIDEWAYS or (held and lo <= min(b for b, _ in held) * (1 - stop)):
            for bp, sp in held:
                cash += cl * (sp * (1 - FEE) / bp) * (1 - FEE)
            held = []
        elif conf == regime.SIDEWAYS:
            nh = []
            for bp, sp in held:
                tgt = bp * (1 + step)
                if hi >= tgt:
                    cash += tgt * (sp * (1 - FEE) / bp) * (1 - FEE)
                else:
                    nh.append((bp, sp))
            held = nh
            ref = min(b for b, _ in held) if held else cl
            trig = ref * (1 - step) if held else cl
            if len(held) < n and lo <= trig and cash >= per:
                held.append((trig, per))
                cash -= per
        eqc.append(cash + sum(cl * (sp * (1 - FEE) / bp) for bp, sp in held))
    final = cash + sum(float(rows.iloc[-1]["close"]) * (sp * (1 - FEE) / bp) for bp, sp in held)
    eq = pd.Series(eqc)
    mdd = ((eq - eq.cummax()) / eq.cummax()).min() * 100 if len(eq) else 0.0
    return (final / budget - 1) * 100, mdd


def main():
    cfg = yaml.safe_load(open(os.path.join(BASE, "config.yaml"), encoding="utf-8"))
    step = 0.012
    n = 8
    stop = 0.05
    print(f"  그리드 박스 (step {step*100:g}% × {n}칸, 바닥손절 {stop*100:g}%) — 실 KRW 10종목")
    print(f"  {'국면':<8}{'그리드 평균/코인':>16}{'+코인%':>8}{'평균MDD':>9}")
    for reg in ["sideways", "bear"]:
        data = cb.load_csv(reg, cfg)
        if not data:
            print(f"  {reg}: 데이터 없음 (backtest_data 필요)"); continue
        res = [grid_sim(df, cfg, step, n, stop) for tk, df in data.items()]
        r = np.array([x[0] for x in res]); m = np.mean([x[1] for x in res])
        print(f"  {reg:<8}{r.mean():>+15.3f}%{(r>0).mean()*100:>7.0f}%{m:>8.2f}%")
    print("\n  참고(현행 단일박스): 횡보 -0.19% / 약세 -1.40%  → 그리드가 비국면 손실 4배↓")


if __name__ == "__main__":
    main()
