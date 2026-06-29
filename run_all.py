"""통합 실행기 — 검증된 3엔진(코어·폭락·횡보)을 한 프로세스에서 병행 구동하고
3단 통합 대시보드를 띄운다.

  🪨 코어(LongTrendTrader, 추세)  ·  💥 폭락(CapitulationTrader, 급락)  ·  📦 횡보(SidewaysTrader, 범위)
  셋 다 같은 업비트 계정 공유(장부 분리). 각 패널에서 개별 시작/정지.
  적립(ProfitSkim): 합산자산 새 고점마다 수익 절반 적립(원금보존 현금흐름).

사용법:
    python run_all.py            # config.yaml 사용
  브라우저: http://127.0.0.1:8000   (매매는 각 패널 [시작] 버튼으로 활성화 — 안전 기본 정지)
  ⚠️ mode: live 면 실주문이 나간다. 충분히 모의 검증 후 소액으로. 출금권한 금지.
"""
from __future__ import annotations

import os
import sys
import threading
import time
import traceback

import uvicorn
import yaml

from trader.capitulation import CapitulationTrader
from trader.longtrend import LongTrendTrader
from trader.safety import Notifier, RiskGuard
from trader.sideways import SidewaysTrader
from trader.skim import ProfitSkim
from trader.telegram_control import TelegramControl
from web.app import create_combined_app

BASE = os.path.dirname(os.path.abspath(__file__))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def load_dotenv(path: str) -> None:
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val


def _start_monitor(core, capit, side, guard, skim, notifier, cfg, mode):
    """감시 스레드: 합산자산→차단기·적립, 새 체결·에러 알림, 생존신호(heartbeat)."""
    poll = max(int(cfg.get("safety", {}).get("poll_sec", 30)), 10)
    hb_min = float(cfg.get("safety", {}).get("heartbeat_min", 60))
    state = {"seen_trade": "", "last_err": "", "last_hb": 0.0}
    engines = [("코어", core), ("폭락", capit), ("횡보", side)]

    def equity_of():
        snaps = {nm: e.status() for nm, e in engines}
        tot = sum(s["total_equity"] for s in snaps.values())
        return snaps, tot

    def collect_trades():
        # 엔진의 실제 체결기록(engine.trades)을 직접 읽음 — 폭락/횡보 체결도 잡힘
        rows = []
        for nm, e in engines:
            for t in (getattr(e, "trades", []) or [])[-40:]:
                rows.append((t.get("time", ""), nm, t))
        return sorted(rows)

    def loop():
        # 시작 시점 이전 체결은 무시(재시작 시 과거거래 알림폭탄 방지)
        init = collect_trades()
        state["seen_trade"] = init[-1][0] if init else ""
        notifier.send(f"🤖 <b>SuperPro 가동</b> ({'실거래' if mode=='live' else '모의'}) — "
                      f"코어+폭락+횡보 감시. 차단기 낙폭 -{guard.max_dd*100:.0f}%/일일 -{guard.max_daily*100:.0f}%")
        while True:
            try:
                snaps, eq_tot = equity_of()
                # 1) 차단기 + 적립(고점 50%)
                halted = guard.update(eq_tot)
                core.set_halt(halted); capit.set_halt(halted); side.set_halt(halted)
                sk = skim.update(eq_tot)
                # 2) 새 체결 알림 — 매도는 수익/손실 명확히
                if notifier.notify_trades:
                    for tm, nm, t in collect_trades():
                        if not tm or tm <= state["seen_trade"]:
                            continue
                        state["seen_trade"] = tm
                        tk = (t.get("ticker") or "").replace("KRW-", "")
                        price = t.get("price", 0) or 0
                        if t.get("side") == "sell":
                            pnl = t.get("pnl", 0) or 0
                            emo = "💰 <b>수익실현</b>" if pnl >= 0 else "🔻 <b>손실</b>"
                            notifier.send(f"{emo} · {nm} {tk}\n{pnl:+,.0f}원 @ {price:,.0f} "
                                          f"<i>{t.get('reason','')}</i>")
                        else:
                            notifier.send(f"🟦 매수 · {nm} {tk} @ {price:,.0f}")
                # 3) 에러 알림
                if notifier.notify_errors:
                    err = next((s.get("error") for s in snaps.values() if s.get("error")), "")
                    if err and err != state["last_err"]:
                        state["last_err"] = err
                        notifier.send(f"⚠️ <b>엔진 오류</b>\n{err.splitlines()[-1][:200]}")
                # 4) 생존신호
                now = time.time()
                if hb_min > 0 and now - state["last_hb"] >= hb_min * 60:
                    state["last_hb"] = now
                    tag = "🔴차단중" if halted else "🟢정상"
                    real = sum(s.get("total_realized", 0) for s in snaps.values())
                    base = sum(s.get("total_base", 0) for s in snaps.values())
                    pnl = eq_tot - base
                    res = f" · 적립 {sk['reserve']:,.0f}원" if sk.get("enabled") else ""
                    notifier.send(f"💓 {tag} 합산 {eq_tot:,.0f}원 ({pnl:+,.0f} · 실현 {real:+,.0f}){res}")
            except Exception:
                if notifier.notify_errors:
                    notifier.send(f"⚠️ 감시 스레드 예외\n{traceback.format_exc(limit=2)[:200]}")
            time.sleep(poll)

    threading.Thread(target=loop, daemon=True).start()


def main():
    load_dotenv(os.path.join(BASE, ".env"))
    cfg_name = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    cfg_path = cfg_name if os.path.isabs(cfg_name) else os.path.join(BASE, cfg_name)
    cfg = yaml.safe_load(open(cfg_path, encoding="utf-8"))
    mode = cfg.get("mode", "paper")

    # 실거래: 한 계정 공유 → 엔진별 장부기반 브로커(자기 서브예산/수량만). 모의면 None.
    core_broker = capit_broker = side_broker = None
    if mode == "live":
        from trader.account import LedgerBroker
        up = cfg.get("upbit", {})
        fee = float(cfg.get("trade", {}).get("fee", 0.0005))
        core_broker = LedgerBroker(up.get("access_key", ""), up.get("secret_key", ""), fee=fee)
        capit_broker = LedgerBroker(up.get("access_key", ""), up.get("secret_key", ""), fee=fee)
        side_broker = LedgerBroker(up.get("access_key", ""), up.get("secret_key", ""), fee=fee)

    # 코어(중장기 추세추종)
    core = LongTrendTrader(cfg, state_path=os.path.join(BASE, "data", f"longterm_{mode}.json"),
                           broker=core_broker)
    # 폭락엣지(무상관 보너스 엔진)
    capit = CapitulationTrader(cfg, state_path=os.path.join(BASE, "data", f"capitulation_{mode}.json"),
                               broker=capit_broker)
    # 횡보(범위 평균회귀 — 그리드 대체, 검증 통과)
    side = SidewaysTrader(cfg, state_path=os.path.join(BASE, "data", f"sideways_{mode}.json"),
                          broker=side_broker)

    core.start_loop()
    capit.start_loop()
    side.start_loop()       # 세 엔진 평가 루프 기동(매매는 대시보드에서 활성화)

    # ── 운영 안전망: 차단기 + 적립 + 텔레그램 알림 감시 스레드 ──
    notifier = Notifier(cfg)
    guard = RiskGuard(cfg, state_path=os.path.join(BASE, "data", f"riskguard_{mode}.json"),
                      notifier=notifier)
    skim = ProfitSkim(cfg, state_path=os.path.join(BASE, "data", f"skim_{mode}.json"), notifier=notifier)
    _start_monitor(core, capit, side, guard, skim, notifier, cfg, mode)

    # ── 텔레그램 양방향 원격제어(폰에서 /상태 /정지 등) ──
    control = TelegramControl(cfg, {"core": core, "capit": capit, "side": side},
                              skim=skim, guard=guard)
    control.start_loop()

    app = create_combined_app(core, capit, side, skim)
    print("=" * 64)
    print(f"  SuperPro 통합 대시보드  ({'실거래' if mode=='live' else '모의'} 모드)")
    core_total = sum(core._per_coin(tk) for tk in core.tickers)
    print(f"  🪨 코어 {core_total:,.0f}  |  💥 폭락 {capit.budget:,.0f}(공유풀)"
          f"  |  📦 횡보 {side.budget:,.0f}(공유풀)")
    if skim.enabled:
        print(f"  💰 적립: 고점대비 수익 {skim.ratio*100:.0f}% 자동적립 (현재 인출가능 {skim.reserve:,.0f}원)")
    if mode == "live":
        print("  ⚠️ 실거래: 같은 계정 공유. reconcile.py 로 정합성 점검 권장. 출금권한 금지.")
    print("  http://127.0.0.1:8000   (매매는 각 패널 [시작] 버튼)")
    print("=" * 64)
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")


if __name__ == "__main__":
    main()
