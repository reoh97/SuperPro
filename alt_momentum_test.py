"""작은 알트 '급등 잡아 짧게 타기' 검증 — 사후선택 편향 없이 전 종목 실시간식 테스트.

scan_pumpers.py로 모은 alt_data/(업비트 전 종목)에 급등감지 진입을 흘려보낸다.
  진입: 최근 K봉 급등(>surge) + 거래량 폭발 + 양봉  (실시간처럼, 어느 코인이 뛸지 모르고)
  청산: 트레일(고점-trail%)로 펌프를 끝까지 타다 꺾이면 청산  /  손절  /  타임아웃
비용: 수수료 왕복 0.1% + 슬리피지(작은 알트는 호가 얇음 → 보수적으로 추가 차감).

→ 메이저(실패)와 달리 진짜 알트에선 다른가? 슬리피지 넣고도 +면 가치, 아니면 닫음.
사용법: python alt_momentum_test.py   (alt_data/ 필요 — 집에서 scan_pumpers.py 후 push)
"""
from __future__ import annotations
import glob, os, sys, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pandas as pd
import yaml
from trader import indicators

FEE = 0.0005          # 편도 수수료
SLIP = 0.003          # 슬리피지 가정(작은 알트 호가 얇음, 왕복 추정). 보수적으로.

try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass


def run_coin(e, K, surge, vmult, trail, sl, hold):
    c = e["close"].to_numpy(float); h = e["high"].to_numpy(float)
    lo = e["low"].to_numpy(float); o = e["open"].to_numpy(float)
    vol = e["volume"].to_numpy(float); vma = e["vol_ma"].to_numpy(float)
    eq = 1.0; pos = None; pnls = []
    for i in range(210, len(c)):
        if pos is not None:
            pos["peak"] = max(pos["peak"], h[i])
            stop = max(pos["sl_p"], pos["peak"]*(1-trail))
            ex = None
            if lo[i] <= stop: ex = stop
            elif i-pos["i0"] >= hold: ex = c[i]
            if ex is not None:
                ret = ex/pos["entry"]-1 - 2*FEE - SLIP    # 수수료+슬리피지 차감
                pnls.append(ret); pos = None
            continue
        if i-K < 0 or not (vma[i] > 0): continue
        if c[i]/c[i-K]-1 >= surge and vol[i] >= vma[i]*vmult and c[i] > o[i]:
            pos = {"entry": c[i], "peak": h[i], "sl_p": c[i]*(1-sl), "i0": i}
    if pos is not None:
        pnls.append(c[-1]/pos["entry"]-1 - 2*FEE - SLIP)
    return pnls


def main():
    files = sorted(glob.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)), "alt_data", "*.csv")))
    if not files:
        print("alt_data/ 없음 — 집에서 scan_pumpers.py 실행 후 push 하세요."); return
    cfg = yaml.safe_load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml"), encoding="utf-8"))
    print("="*70)
    print(f"  작은 알트 '급등 잡아 타기' 검증 — {len(files)}종목 (수수료0.1%+슬리피지{SLIP*100:.1f}% 차감)")
    print("="*70)
    grids = [
        {"K":2,"surge":0.05,"vmult":3.0,"trail":0.04,"sl":0.03,"hold":16},
        {"K":3,"surge":0.07,"vmult":3.0,"trail":0.05,"sl":0.04,"hold":24},
        {"K":2,"surge":0.10,"vmult":4.0,"trail":0.06,"sl":0.05,"hold":32},
    ]
    for p in grids:
        allp = []
        for f in files:
            df = pd.read_csv(f, index_col=0, parse_dates=True)
            if len(df) < 260: continue
            e = indicators.enrich(df, cfg)
            allp += run_coin(e, **p)
        if not allp:
            print(f"\n  급등≥{p['surge']*100:.0f}%: 진입 없음"); continue
        a = np.array(allp); w = a[a>0]; l = a[a<0]
        pf = w.sum()/-l.sum() if len(l) else 9.9
        print(f"\n  급등≥{p['surge']*100:.0f}% trail{p['trail']*100:.0f}%: 거래 {len(a)}회  "
              f"평균 {a.mean()*100:+.2f}%/거래  승률 {(a>0).mean()*100:.0f}%  PF {pf:.2f}  "
              f"총 {a.sum()*100:+.0f}%")
    print("\n  판정: 평균/거래가 +이고 PF>1.3이면 슬리피지 넘는 진짜 엣지. -면 펌프추격=손실 확정.")


if __name__ == "__main__":
    main()
