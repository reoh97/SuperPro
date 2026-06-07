"""동적 셀렉터 검증 — '적합도 점수로 고른 상위3'이 실제로 잘 버는가? (아웃오브샘플)

방법(미래정보 차단):
  - 각 국면 윈도우를 [선별구간 | 성과구간]으로 분할.
  - 선별구간 끝에서 fitness 점수 계산 → 상위3 선정 (성과구간 정보 안 씀).
  - 성과구간을 라이브 엔진으로 돌려 코인별 forward 수익 측정.
  - 비교: ①셀렉터 상위3  ②전체10 평균  ③오라클(사후 최고3) — 셀렉터가 ②보다 높고 ③에 가까우면 OK.
사용법: python validate_selector.py
"""
from __future__ import annotations
import os, sys, numpy as np, yaml
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import circuit_backtest as cb
from confluence_live_check import replay_coin, _AIView
from trader import selector

try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass

SPLIT = 800        # 선별구간 길이(봉). 이후가 성과구간.


def run_regime(cfg, label, view, budget):
    data = cb.load_csv(label, cfg)
    # 선별 신호: 선별구간까지만 사용
    sig_data = {tk: df.iloc[:SPLIT] for tk, df in data.items()}
    scores = selector.score_table(sig_data, er_period=240, mom_period=240, mom_floor=-0.08)
    picks = selector.rank_coins(sig_data, top_k=3, er_period=240, mom_period=240, mom_floor=-0.08)
    # forward 수익: 성과구간(워밍업 위해 split-210부터 슬라이스)
    fwd = {}
    for tk, df in data.items():
        sub = df.iloc[SPLIT - 210:]
        fwd[tk] = replay_coin(cfg, tk, sub, view, budget)["ret"]
    allret = np.array(list(fwd.values()))
    pick_ret = np.array([fwd[t] for t in picks])
    oracle = sorted(fwd, key=lambda t: fwd[t], reverse=True)[:3]
    orc_ret = np.array([fwd[t] for t in oracle])
    print(f"\n  ===== {label} =====")
    print(f"  적합도 점수순: " + ", ".join(f"{t.split('-')[1]}={scores[t]:.2f}"
          for t in sorted(scores, key=lambda x: scores[x], reverse=True)))
    print(f"  forward 수익순: " + ", ".join(f"{t.split('-')[1]}={fwd[t]:+.1f}%"
          for t in sorted(fwd, key=lambda x: fwd[x], reverse=True)))
    print(f"  ① 셀렉터 상위3 {[t.split('-')[1] for t in picks]}: 평균 {pick_ret.mean():+.2f}%")
    print(f"  ② 전체 10종목           : 평균 {allret.mean():+.2f}%")
    print(f"  ③ 오라클(사후최고3) {[t.split('-')[1] for t in oracle]}: 평균 {orc_ret.mean():+.2f}%")
    hit = len(set(picks) & set(oracle))
    print(f"  → 셀렉터 vs 전체: {pick_ret.mean()-allret.mean():+.2f}%p / 오라클 적중 {hit}/3")
    return pick_ret.mean(), allret.mean(), orc_ret.mean()


def main():
    cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
    budget = 2_000_000
    print("=" * 70)
    print(f"  동적 셀렉터 검증 (threshold={cfg['confluence']['threshold']}, 선별구간 {SPLIT}봉)")
    print("=" * 70)
    res = []
    res.append(run_regime(cfg, "bull", _AIView("BULL", confidence=80), budget))
    res.append(run_regime(cfg, "sideways", None, budget))
    p = np.mean([r[0] for r in res]); a = np.mean([r[1] for r in res]); o = np.mean([r[2] for r in res])
    print("\n  " + "-" * 60)
    print(f"  종합: 셀렉터 {p:+.2f}%  vs  전체10 {a:+.2f}%  vs  오라클 {o:+.2f}%")
    print(f"  셀렉터가 전체보다 +{p-a:.2f}%p, 오라클 대비 {p/o*100 if o else 0:.0f}% 포착이면 채택.")


if __name__ == "__main__":
    main()
