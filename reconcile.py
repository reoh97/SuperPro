"""단일계정 정합성 점검 — 코어+새틀 두 엔진의 장부 합 vs 실제 업비트 잔고 대조.

계정분리 없이 한 계정에서 두 엔진을 돌릴 때, '장부(각 엔진 state) 합'과 '실계정 잔고'가
일치하는지 주기적으로 확인한다. 어긋나면(부분체결·슬리피지 누적·상대코인 매도·수동입출금)
경보를 띄워 조기에 잡는다.

사용법:
    python reconcile.py                # 기본 state 파일들 + config.yaml 키
  (실거래 키 필요: config.yaml upbit.* 또는 환경변수 UPBIT_ACCESS_KEY/SECRET_KEY)
  cron/스케줄러로 N분마다 돌리면 상시 감시가 된다. 종료코드: 정합 0 / 경보 1 / 오류 2.
"""
from __future__ import annotations
import json, os, sys
import yaml

from trader.account import reconcile

BASE = os.path.dirname(os.path.abspath(__file__))
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass


def _load(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def main():
    cfg = yaml.safe_load(open(os.path.join(BASE, "config.yaml"), encoding="utf-8"))
    mode = cfg.get("mode", "paper")
    # 엔진별 state 파일(모드 접미사 일치). 인자로 직접 줄 수도 있음.
    sat = sys.argv[1] if len(sys.argv) > 1 else os.path.join(BASE, "data", f"live_{mode}.json")
    core = sys.argv[2] if len(sys.argv) > 2 else os.path.join(BASE, "data", f"longterm_{mode}.json")
    ledgers = [("새틀(live)", _load(sat)), ("코어(longterm)", _load(core))]

    if not ledgers[0][1] and not ledgers[1][1]:
        print(f"장부 파일이 비었습니다. ({sat} / {core}) — 엔진을 먼저 돌리세요."); return 2

    import pyupbit
    up = cfg.get("upbit", {})
    access = up.get("access_key", "") or os.environ.get("UPBIT_ACCESS_KEY", "")
    secret = up.get("secret_key", "") or os.environ.get("UPBIT_SECRET_KEY", "")
    if not access or not secret:
        print("⚠️ 업비트 API 키가 없습니다(config.yaml upbit.* 또는 환경변수). 잔고 대조 불가."); return 2
    upbit = pyupbit.Upbit(access, secret)

    rep = reconcile(ledgers, upbit)

    print("=" * 64)
    print(f"  단일계정 정합성 점검  (mode={mode})")
    print("=" * 64)
    k = rep["krw"]
    print(f"  KRW   장부 {k.get('expected',0):>14,.0f}  실계정 {k.get('real',0):>14,.0f}  "
          f"드리프트 {k.get('drift',0):>+12,.0f}")
    for cur, d in rep.get("coins", {}).items():
        print(f"  {cur:<5} 장부 {d['expected']:>14.8f}  실계정 {d['real']:>14.8f}  "
              f"드리프트 {d['drift']:>+12.8f}")
    print("-" * 64)
    if rep["ok"]:
        print("  ✅ 정합성 OK — 장부와 실계정이 허용오차 내 일치.")
        return 0
    for a in rep["alerts"]:
        print(f"  {a}")
    print("\n  → 드리프트 원인 점검: 부분체결/슬리피지 누적·수동입출금·코드버그. "
          "필요시 엔진 정지 후 state 수동 보정.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
