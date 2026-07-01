"""텔레그램 양방향 제어 — 폰에서 명령 보내면 봇이 듣고 실행/응답.

Notifier(일방 알림)와 짝. getUpdates 롱폴링으로 명령 수신 → 엔진 제어 → 결과 회신.
보안: 설정된 chat_id가 보낸 명령만 실행(타인이 자금 제어 불가). 표준 라이브러리만 사용.

명령(슬래시/한글 둘 다):
  /상태  /status        — 전 엔진 자산·보유·차단 상태 보고
  /정지  /stop          — 전 엔진 정지(완전 멈춤)
  /시작  /start         — 전 엔진 시작
  /정지 코어|폭락|횡보   — 특정 엔진만 정지   (/start 코어 = 특정만 시작)
  /매수중단 /halt        — 신규매수만 중단(보유분은 전략대로 청산 계속)
  /해제  /resume        — 매수중단 해제
  /도움  /help          — 명령 목록
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.parse
import urllib.request
from typing import Optional


def _won(n):
    try: return f"{n:,.0f}원"
    except Exception: return "-"


class TelegramControl:
    NAME = {"코어": "core", "core": "core", "폭락": "capit", "capit": "capit",
            "횡보": "side", "side": "side"}
    LABEL = {"core": "🪨코어", "capit": "💥폭락", "side": "📦횡보"}

    def __init__(self, cfg: dict, engines: dict, skim=None, guard=None):
        tg = cfg.get("telegram", {})
        self.token = tg.get("bot_token", "") or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = str(tg.get("chat_id", "") or os.environ.get("TELEGRAM_CHAT_ID", ""))
        self.enabled = bool(tg.get("enabled", False)) and bool(self.token) and bool(self.chat_id) \
            and bool(tg.get("control", True))
        self.engines = engines            # {"core":.., "capit":.., "side":..}
        self.skim = skim
        self.guard = guard
        self._offset = 0
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ---------- 송수신 ----------
    def _send(self, text: str):
        try:
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            data = urllib.parse.urlencode({"chat_id": self.chat_id, "text": text,
                                           "parse_mode": "HTML"}).encode()
            urllib.request.urlopen(url, data=data, timeout=10)
        except Exception:
            pass

    def _get_updates(self):
        url = (f"https://api.telegram.org/bot{self.token}/getUpdates"
               f"?timeout=30&offset={self._offset}")
        with urllib.request.urlopen(url, timeout=40) as r:
            return json.loads(r.read().decode()).get("result", [])

    # ---------- 스레드 ----------
    def start_loop(self):
        if not self.enabled or (self._thread and self._thread.is_alive()):
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._send("🎮 <b>원격제어 켜짐</b> — /도움 으로 명령 목록. /상태 로 현황.")

    def _run(self):
        while not self._stop.is_set():
            try:
                for u in self._get_updates():
                    self._offset = u["update_id"] + 1
                    msg = u.get("message") or u.get("channel_post") or {}
                    chat = str((msg.get("chat") or {}).get("id", ""))
                    text = (msg.get("text") or "").strip()
                    if not text:
                        continue
                    if chat != self.chat_id:        # 타인 명령 무시(보안)
                        continue
                    self._handle(text)
            except Exception:
                time.sleep(3)   # 네트워크 블립 → 잠시 후 재시도

    # ---------- 명령 처리 ----------
    def _handle(self, text: str):
        parts = text.replace("/", " ").split()
        if not parts:
            return
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else None
        target = self.NAME.get(arg) if arg else None

        if cmd in ("상태", "status"):
            self._send(self._status_text())
        elif cmd in ("시작", "start"):
            self._apply("enable", target);
            self._send((f"▶️ {self.LABEL[target]} 시작" if target else "▶️ 전 엔진 시작") + "\n" + self._status_text())
        elif cmd in ("정지", "stop"):
            self._apply("disable", target)
            self._send((f"⏹ {self.LABEL[target]} 정지" if target else "⏹ 전 엔진 정지(완전 멈춤)") + "\n" + self._status_text())
        elif cmd in ("매수중단", "halt"):
            self._apply("halt_on", None)
            self._send("🟠 신규매수 중단 — 보유분은 전략대로 청산 계속. /해제 로 재개.")
        elif cmd in ("해제", "resume"):
            self._apply("halt_off", None)
            self._send("🟢 매수중단 해제 — 신규진입 재개.")
        elif cmd in ("매도", "팔아", "sell"):
            if not arg:
                self._send("종목을 지정하세요. 예: /매도 ADA"); return
            sym = arg.upper().replace("KRW-", "")
            ticker = "KRW-" + sym
            found = []
            for k, e in self.engines.items():
                try:
                    s = e.status()
                except Exception:
                    continue
                holds = any(c.get("holding") and c.get("ticker") == ticker for c in s.get("coins", []))
                if holds and hasattr(e, "request_sell"):
                    e.request_sell(ticker); found.append(self.LABEL[k])
            if found:
                self._send(f"📤 <b>매도 요청</b> {sym} — {', '.join(found)}\n"
                           f"곧 시장가 청산(1초 내). 완료되면 체결 알림이 갑니다.")
            else:
                self._send(f"❓ {sym} 보유 포지션이 없어요. /상태로 확인하세요.")
        elif cmd in ("거래", "기록", "history"):
            rows = []
            for k, e in self.engines.items():
                for t in (getattr(e, "trades", []) or [])[-15:]:
                    rows.append((t.get("time", ""), self.LABEL[k], t))
            rows.sort(key=lambda r: r[0])
            rows = rows[-12:]
            if not rows:
                self._send("📒 아직 거래 없음"); return
            out = ["📒 <b>최근 거래</b>"]
            for tm, lbl, t in rows:
                tk = (t.get("ticker") or "").replace("KRW-", "")
                when = tm[5:16] if len(tm) >= 16 else tm
                if t.get("side") == "sell":
                    pnl = t.get("pnl", 0) or 0
                    fee = t.get("fee", 0) or 0
                    mk = "💰" if pnl >= 0 else "🔻"
                    out.append(f"{when} {lbl}{tk} {mk}{pnl:+,.0f}원 (수수료{fee:,.0f}·{t.get('reason','')})")
                else:
                    out.append(f"{when} {lbl}{tk} 🟦매수 {t.get('amount',0):,.0f}원")
            self._send("\n".join(out))
        elif cmd in ("도움", "help", "?"):
            self._send(self._help_text())
        else:
            self._send("❓ 모르는 명령. /도움")

    def _apply(self, action: str, target: Optional[str]):
        targets = [target] if target else list(self.engines)
        for k in targets:
            e = self.engines.get(k)
            if e is None:
                continue
            if action == "enable": e.enable()
            elif action == "disable": e.disable()
            elif action == "halt_on": e.set_halt(True)
            elif action == "halt_off": e.set_halt(False)

    def _status_text(self) -> str:
        SUB = {"core": "추세·메이저", "capit": "급락·무상관", "side": "범위·메이커"}
        eng_lines = []
        tot_eq = tot_base = tot_real = 0.0
        for k, e in self.engines.items():
            s = e.status()
            eq = s.get("total_equity", 0); base = s.get("total_base", 0)
            real = s.get("total_realized", 0)
            tot_eq += eq; tot_base += base; tot_real += real
            run = "🟢가동" if s.get("running") else "⏸정지"
            pnl = eq - base; pct = (pnl / base * 100) if base else 0.0
            blk = (f"{self.LABEL[k]} <i>{SUB.get(k,'')}</i> · {run}\n"
                   f"   평가 {_won(eq)} ({pnl:+,.0f} · {pct:+.2f}%)")
            if real:
                blk += f" · 실현 {real:+,.0f}"
            held = [c for c in s.get("coins", []) if c.get("holding")]
            if held:
                for c in held:
                    sym = c.get("ticker", "").replace("KRW-", "")
                    unrl = c.get("unrealized", c.get("unrl", 0)) or 0
                    inv = c.get("amount", 0) or 0
                    be = c.get("breakeven"); cur = c.get("price")
                    mark = ""
                    if be and cur:
                        mark = " 🟢본전↑" if cur >= be else " 🔴본전↓"
                    cur_txt = f"현재 {cur:,.0f} · " if cur else ""
                    be_txt = f"본전 {be:,.0f}" if be else ""
                    blk += (f"\n   • {sym} {inv:,.0f}원어치{mark}\n"
                            f"     {cur_txt}평단 {c.get('avg',0):,.0f} · {be_txt} ({unrl:+,.0f})")
            else:
                blk += "\n   • 보유 없음"
            eng_lines.append(blk)
        tot_pnl = tot_eq - tot_base
        tot_pct = (tot_pnl / tot_base * 100) if tot_base else 0.0
        head = ("📊 <b>SuperPro 현황</b> (모의)\n"
                "━━━━━━━━━━━━━━\n"
                f"💰 합산자산 <b>{_won(tot_eq)}</b>\n"
                f"📈 손익 {tot_pnl:+,.0f} ({tot_pct:+.2f}%)\n"
                f"✅ 실현손익 {tot_real:+,.0f}\n")
        if self.skim is not None:
            sk = self.skim.status()
            if sk.get("enabled"):
                head += f"🏦 그중 적립(인출가능) {_won(sk.get('reserve', 0))}\n"
        if self.guard is not None:
            g = self.guard.status()
            head += ("🔴 <b>차단중</b> — 신규진입 정지\n" if g.get("halted")
                     else "🛡 차단기 정상\n")
        return head + "━━━━━━━━━━━━━━\n" + "\n\n".join(eng_lines)

    def _help_text(self) -> str:
        return ("🎮 <b>명령 목록</b>\n"
                "/상태 — 자산·보유·차단 현황\n"
                "/시작 — 전 엔진 시작 (/시작 코어 = 코어만)\n"
                "/정지 — 전 엔진 정지 (/정지 횡보 = 횡보만)\n"
                "/매수중단 — 신규매수만 중단(청산은 계속)\n"
                "/해제 — 매수중단 해제\n"
                "/매도 ADA — 그 종목 즉시 시장가 청산(수동)\n"
                "/거래 — 최근 체결 내역\n"
                "엔진명: 코어 · 폭락 · 횡보")
