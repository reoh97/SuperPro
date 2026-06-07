"""선별 신호 탐색 — '다음 구간에 잘 갈 코인'을 맞히는 신호가 있는가? (롤링 재선별)

라이브처럼 매 rebalance마다 신호로 상위3을 고른다고 가정하고, 그 3코인의 '다음 구간
가격 forward수익'이 전체 평균보다 높은지 본다(가격기반=빠름. 엔진은 롱바이어스라 forward수익↑이면 유리).
여러 신호를 한꺼번에 비교 → 전체평균을 못 이기면 동적선별은 무의미(고정 바스켓이 정답).
사용법: python signal_scan.py
"""
from __future__ import annotations
import os, sys, numpy as np, yaml
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import circuit_backtest as cb

try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass

REBAL = 96     # 재선별 주기(봉). 15분봉 96 = 1일
HORIZON = 96   # forward 평가 구간(봉)
WARM = 300     # 신호 계산 최소 과거봉


def er(close, p):
    if len(close) < p + 1: return 0.0
    s = close[-(p+1):]; net = abs(s[-1]-s[0]); path = np.abs(np.diff(s)).sum()
    return net/path if path > 0 else 0.0


def signals(close):
    """여러 후보 신호를 dict로. close=numpy(과거~현재)."""
    c = close
    def mom(p): return (c[-1]/c[-(p+1)]-1) if len(c) > p else 0.0
    return {
        "ER240":      er(c, 240),
        "ER96":       er(c, 96),
        "MOM96":      mom(96),
        "MOM240":     mom(240),
        "MOM48":      mom(48),
        "ER240xMOM":  er(c, 240) * (1 + max(0, mom(240))),
        "ER96xMOM96": er(c, 96) * (1 + max(0, mom(96))),
        "MOM96-vol":  mom(96) / (np.std(np.diff(c[-97:])/c[-97:-1]) + 1e-9) if len(c) > 97 else 0.0,
    }


def scan(label, data, topk=3):
    coins = list(data)
    closes = {tk: data[tk]["close"].to_numpy(float) for tk in coins}
    n = min(len(v) for v in closes.values())
    signames = list(signals(closes[coins[0]][:WARM]).keys())
    # 각 신호별: 선택3 forward 평균, 전체평균 누적
    sel_acc = {s: [] for s in signames}
    all_acc = []
    t = WARM
    while t + HORIZON < n:
        fwd = {tk: closes[tk][t+HORIZON]/closes[tk][t]-1 for tk in coins}
        all_acc.append(np.mean([fwd[tk] for tk in coins]))
        sigvals = {tk: signals(closes[tk][:t]) for tk in coins}
        for s in signames:
            picks = sorted(coins, key=lambda tk: sigvals[tk][s], reverse=True)[:topk]
            sel_acc[s].append(np.mean([fwd[tk] for tk in picks]))
        t += REBAL
    base = np.mean(all_acc) * 100
    print(f"\n  ===== {label} =====  (재선별 {len(all_acc)}회, 전체평균 forward {base:+.3f}%/구간)")
    print(f"  {'신호':<14}{'선택3 평균':>12}{'vs전체':>10}{'승구간%':>9}")
    res = {}
    for s in signames:
        m = np.mean(sel_acc[s]) * 100
        win = np.mean(np.array(sel_acc[s]) > np.array(all_acc)) * 100
        res[s] = m - base
        print(f"  {s:<14}{m:>+11.3f}%{m-base:>+9.3f}%{win:>8.0f}%")
    return res


def main():
    cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
    print("=" * 60)
    print(f"  선별 신호 탐색 (재선별 {REBAL}봉=1일, forward {HORIZON}봉, 상위3)")
    print("=" * 60)
    agg = {}
    for label in ("bull", "sideways", "bear"):
        data = cb.load_csv(label, cfg)
        if not data: continue
        r = scan(label, data)
        for k, v in r.items(): agg[k] = agg.get(k, 0) + v
    print("\n  " + "-"*50)
    print("  종합(국면합산) vs전체 초과수익순:")
    for k in sorted(agg, key=lambda x: agg[x], reverse=True):
        print(f"    {k:<14}{agg[k]:>+8.3f}%p")
    print("\n  → 양수면 그 신호로 동적선별 가치 있음. 전부 0近/음수면 고정 바스켓이 정답.")


if __name__ == "__main__":
    main()
