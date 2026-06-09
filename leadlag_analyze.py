"""lead-lag 분석 — 기록된 틱(또는 1분봉)으로 '바이낸스 선행 → 업비트 추종' 지연을 측정.

record_ticks.py(틱) 또는 fetch_leadlag.py(1분봉)로 받은 데이터를 읽어,
바이낸스가 움직인 뒤 업비트가 '몇 초/밀리초 뒤에, 수수료(0.1%) 넘게' 따라오는지 정량화한다.
→ 진짜 엣지가 있으면 빠른 봇 제작, 없으면(프로가 이미 다 먹음) 깔끔히 포기.

분석 2종:
  1. 교차상관: 바이낸스 수익률을 N초 앞당겨 업비트와 상관 → 상관 최대가 되는 '지연' 탐색.
  2. 이벤트 스터디: 바이낸스가 급등(>임계)한 직후, 업비트의 forward 수익 분포(수수료 넘나?).

사용법:
    python leadlag_analyze.py                       # leadlag_ticks/ 최신 틱파일 자동
    python leadlag_analyze.py leadlag_ticks/xxx.csv # 파일 지정
    python leadlag_analyze.py --bars leadlag_data/KRW-BTC.csv  # 1분봉 동기화파일
"""
from __future__ import annotations

import glob
import os
import sys

import numpy as np
import pandas as pd

FEE_RT = 0.001   # 왕복 수수료 0.1%

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def _series_from_ticks(df, coin):
    """틱 CSV → 코인별 (바이낸스, 업비트) 1초 가격 시계열(recv_ts 기준, ffill)."""
    d = df[df["coin"] == coin].copy()
    d["t"] = pd.to_datetime(d["recv_ts"], unit="s")
    out = {}
    for ex in ("binance", "upbit"):
        s = d[d["exch"] == ex].set_index("t")["price"].sort_index()
        if len(s) == 0:
            return None
        out[ex] = s.resample("1s").last().ffill()
    g = pd.DataFrame(out).dropna()
    return g if len(g) > 60 else None


def analyze_pair(g, coin, step_label="초"):
    """바이낸스·업비트 정렬 시계열 g(컬럼 binance,upbit) → 지연·엣지 리포트."""
    bn = np.log(g["binance"]).diff().fillna(0.0)
    up = np.log(g["upbit"]).diff().fillna(0.0)
    print(f"\n  [{coin}]  {len(g)} {step_label}구간")

    # 1) 교차상관: 바이낸스를 lag만큼 '앞당겨'(shift -lag) 업비트와 상관
    print("     지연별 상관(바이낸스 선행):")
    best_lag, best_c = 0, -9
    line = []
    for lag in range(0, 11):
        c = bn.shift(lag).corr(up)        # lag구간 전 바이낸스 vs 현재 업비트
        line.append(f"{lag}={c:+.2f}")
        if c > best_c:
            best_c, best_lag = c, lag
    print("       " + "  ".join(line))
    print(f"       → 최대상관 지연 {best_lag}{step_label} (상관 {best_c:+.2f})  "
          f"{'(0이면 선행효과 없음=동시)' if best_lag==0 else ''}")

    # 2) 이벤트 스터디: 바이낸스 급등 직후 업비트 forward 수익
    thr = bn.std() * 3                    # 큰 움직임 = 3σ
    hits = bn[bn > thr].index
    if len(hits) >= 5 and best_lag >= 1:
        fwd = []
        gi = g.index
        for t in hits:
            pos = gi.get_indexer([t])[0]
            if 0 <= pos < len(g) - best_lag:
                r = g["upbit"].iloc[pos + best_lag] / g["upbit"].iloc[pos] - 1
                fwd.append(r)
        if fwd:
            fwd = np.array(fwd)
            net = fwd.mean() - FEE_RT
            print(f"     바이낸스 급등({len(fwd)}회) 직후 업비트 {best_lag}{step_label} 수익: "
                  f"평균 {fwd.mean()*100:+.3f}%  (수수료 0.1% 차감 {net*100:+.3f}%)  "
                  f"+비율 {(fwd>0).mean()*100:.0f}%")
            print(f"       → {'★ 엣지 가능(수수료 넘음)' if net>0 else '엣지 없음(수수료 못 넘음)'}")
    else:
        print(f"     급등 이벤트 부족({len(hits)}회) 또는 지연0 → 이벤트 분석 생략")


def main():
    args = sys.argv[1:]
    bars_mode = "--bars" in args
    args = [a for a in args if a != "--bars"]

    print("=" * 66)
    print("  lead-lag 분석 — 바이낸스 선행 → 업비트 추종 지연/엣지 측정")
    print(f"  (수수료 왕복 {FEE_RT*100:.1f}% 차감 기준)")
    print("=" * 66)

    if bars_mode:
        path = args[0] if args else None
        if not path or not os.path.exists(path):
            print("  1분봉 파일 경로를 주세요: --bars leadlag_data/KRW-BTC.csv"); return
        g = pd.read_csv(path, index_col=0, parse_dates=True)
        g = g.rename(columns={c: c.lower() for c in g.columns})[["binance", "upbit"]].dropna()
        analyze_pair(g, os.path.basename(path)[:-4], step_label="분")
        return

    path = args[0] if args else None
    if not path:
        files = sorted(glob.glob("leadlag_ticks/ticks_*.csv"))
        if not files:
            print("  틱 파일 없음 — 집에서 record_ticks.py 로 수집 후 push 하세요."); return
        path = files[-1]
    print(f"  파일: {path}")
    df = pd.read_csv(path)
    for coin in df["coin"].dropna().unique():
        g = _series_from_ticks(df, coin)
        if g is not None:
            analyze_pair(g, coin)
        else:
            print(f"\n  [{coin}] 데이터 부족 — 더 길게 수집 필요")
    print("\n  판정: 급등직후 순수익(수수료차감)이 +이고 +비율 높으면 → 빠른봇 제작 가치.")
    print("        0/마이너스면 프로가 이미 다 먹은 것 → 포기가 정답.")


if __name__ == "__main__":
    main()
