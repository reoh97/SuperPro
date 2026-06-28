"""작은 알트 '급등 잡아 짧게 타기' 검증 — 사후선택 편향 없이 전 종목 실시간식 테스트.

scan_pumpers.py로 모은 alt_data/(업비트 전 종목)에 급등감지 진입을 흘려보낸다.
  진입: 최근 K봉 급등(>surge) + 거래량 폭발 + 양봉  (실시간처럼, 어느 코인이 뛸지 모르고)
  청산: 트레일(고점-trail%)로 펌프를 끝까지 타다 꺾이면 청산  /  손절  /  타임아웃

★ 알트펌프의 3대 함정을 정면으로 검증:
  ① 슬리피지 — 작은 알트는 호가 얇아 펌프 추격 진입이 1~2%씩 밀림 → 0.3~1.5% 민감도로 본다
  ② OOS — 전/후반 갈라 같은 부호 유지되나(과최적화 차단)
  ③ 생존편향 — alt_data엔 살아남은 코인만 있음(펌프&덤프 상폐 코인 누락) → 결과는 '낙관 상한'

사용법: python alt_momentum_test.py   (alt_data/ 필요 — 집에서 scan_pumpers.py 후 push)
"""
from __future__ import annotations
import glob, os, sys, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pandas as pd
import yaml
from trader import indicators

FEE = 0.0005          # 편도 수수료

try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass


def run_coin(e, K, surge, vmult, trail, sl, hold, slip):
    """급등 추격 진입→트레일 청산. (진입시각, 순손익) 리스트 반환. slip=편도 슬리피지."""
    c = e["close"].to_numpy(float); h = e["high"].to_numpy(float)
    lo = e["low"].to_numpy(float); o = e["open"].to_numpy(float)
    vol = e["volume"].to_numpy(float); vma = e["vol_ma"].to_numpy(float)
    idx = e.index
    pos = None; out = []
    for i in range(210, len(c)):
        if pos is not None:
            pos["peak"] = max(pos["peak"], h[i])
            stop = max(pos["sl_p"], pos["peak"]*(1-trail))
            ex = None
            if lo[i] <= stop: ex = stop
            elif i-pos["i0"] >= hold: ex = c[i]
            if ex is not None:
                ret = ex/pos["entry"]-1 - 2*FEE - 2*slip   # 진입+청산 양편 슬리피지
                out.append((idx[pos["i0"]], ret)); pos = None
            continue
        if i-K < 0 or not (vma[i] > 0): continue
        if c[i]/c[i-K]-1 >= surge and vol[i] >= vma[i]*vmult and c[i] > o[i]:
            pos = {"entry": c[i], "peak": h[i], "sl_p": c[i]*(1-sl), "i0": i}
    return out


def stats(arr):
    a = np.array([x[1] for x in arr]) if arr else np.array([])
    if len(a) == 0: return None
    w = a[a > 0]; l = a[a < 0]
    pf = w.sum()/-l.sum() if len(l) else 9.9
    return len(a), a.mean()*100, (a > 0).mean()*100, pf, a.sum()*100


def main():
    BASE = os.path.dirname(os.path.abspath(__file__))
    files = sorted(glob.glob(os.path.join(BASE, "alt_data", "*.csv")))
    if not files:
        print("alt_data/ 없음 — 집에서 scan_pumpers.py 실행 후 push 하세요."); return
    cfg = yaml.safe_load(open(os.path.join(BASE, "config.yaml"), encoding="utf-8"))
    print("="*74)
    print(f"  작은 알트 '급등 추격' 검증 — {len(files)}종목  (생존편향 주의: 낙관 상한값)")
    print("="*74)
    grids = [
        {"K":2,"surge":0.05,"vmult":3.0,"trail":0.04,"sl":0.03,"hold":16},
        {"K":3,"surge":0.07,"vmult":3.0,"trail":0.05,"sl":0.04,"hold":24},
        {"K":2,"surge":0.10,"vmult":4.0,"trail":0.06,"sl":0.05,"hold":32},
    ]
    # 코인별 enrich 캐시
    enr = []
    for f in files:
        df = pd.read_csv(f, index_col=0, parse_dates=True)
        if len(df) < 260: continue
        enr.append(indicators.enrich(df, cfg))

    for p in grids:
        print(f"\n  ─ 급등≥{p['surge']*100:.0f}% trail{p['trail']*100:.0f}% ─")
        print(f"    {'슬립':>5}{'거래':>7}{'평균%':>8}{'승률%':>7}{'PF':>6}{'총%':>8}{'전반평균':>9}{'후반평균':>9}")
        for slip in (0.003, 0.006, 0.010, 0.015):
            allp = []
            for e in enr:
                allp += run_coin(e, **p, slip=slip)
            if not allp:
                print(f"    {slip*100:>4.1f}% 진입 없음"); continue
            allp.sort()
            mid = allp[len(allp)//2][0]
            n, avg, wr, pf, tot = stats(allp)
            f1 = stats([x for x in allp if x[0] <= mid]); f2 = stats([x for x in allp if x[0] > mid])
            mark = "★" if avg > 0 and pf > 1.3 else ("✗" if avg < 0 else "·")
            print(f"    {slip*100:>4.1f}%{n:>7}{avg:>8.2f}{wr:>7.0f}{pf:>6.2f}{tot:>8.0f}"
                  f"{f1[1]:>9.2f}{f2[1]:>9.2f} {mark}")
    print("\n  판정: 슬리피지 키워도 평균+/PF>1.3 유지 + 전·후반 둘다 + 면 진짜 엣지.")
    print("        (생존편향으로 실제는 더 나쁨 — 여기서도 죽으면 확실히 죽은 것)")


if __name__ == "__main__":
    main()
