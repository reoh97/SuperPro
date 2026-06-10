"""중장기 추세추종 엔진 실행기 — 단타봇(run.py)과 별도 프로세스/자본으로 병행.

저빈도(일봉, 코인당 연 몇 회)라 웹 대시보드 없이 콘솔 출력으로 충분.
단타봇과 자본·상태(data/longterm_paper.json) 완전 분리 → 포지션 충돌 없음.

사용법:
    python run_longterm.py        # config.yaml 의 longterm 섹션 사용
  Ctrl+C 로 종료. (상태 저장되어 재시작 시 이어감)
⚠️ 한국망 PC에서 실행(업비트=한국IP). 단타봇과 다른 터미널 창에서 같이 돌리면 됨.
"""
from __future__ import annotations

import os
import sys
import time

import yaml

from trader.longtrend import LongTrendTrader

BASE = os.path.dirname(os.path.abspath(__file__))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def main():
    cfg = yaml.safe_load(open(os.path.join(BASE, "config.yaml"), encoding="utf-8"))
    if not cfg.get("longterm", {}).get("enabled", False):
        print("config.yaml 의 longterm.enabled 가 false 입니다. true 로 바꾸세요."); return

    engine = LongTrendTrader(cfg, state_path=os.path.join(BASE, "data", "longterm_paper.json"))
    engine.start_loop()
    engine.enable()

    print("=" * 60)
    print("  중장기 추세추종 엔진 (일봉 돈키언) — 모의")
    print(f"  종목 {len(engine.tickers)}개 · 종목당 {engine.per_coin:,.0f}원 · "
          f"{engine.entry_n}일돌파/{engine.exit_n}일청산")
    print("  Ctrl+C 로 종료")
    print("=" * 60)

    try:
        while True:
            time.sleep(60)
            st = engine.status()
            held = [c for c in st["coins"] if c["holding"]]
            line = (f"[{st['last_update'] or '...'}] 평가 {st['total_equity']:,.0f}원 "
                    f"({st['total_pnl_pct']:+.2f}%)  보유 {len(held)}/{len(st['coins'])}종목")
            print(line)
            for c in held:
                print(f"    {c['ticker']:<9} 평단 {c['avg']:,.0f} 현재 {c['price']:,.0f} "
                      f"({c['pnl_pct']:+.1f}%)")
            if st["error"]:
                print(f"    ⚠️ {st['error'].splitlines()[-1][:80]}")
    except KeyboardInterrupt:
        engine.shutdown()
        print("\n종료. 상태 저장됨 (재시작 시 이어감).")


if __name__ == "__main__":
    main()
