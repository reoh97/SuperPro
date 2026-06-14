"""운영 안전망 — 포트폴리오 차단기(RiskGuard) + 텔레그램 알림(Notifier).

실전 투입 전 필수: 봇이 폭주(연속손실)하거나 조용히 멈춰도 ① 손실을 막고 ② 알린다.
  - RiskGuard: 합산자산 고점대비 낙폭/일일손실이 한도 초과 시 '신규진입 전면중단'(halt).
    상태는 파일에 저장 → 재시작해도 차단 유지. 새 날엔 일일한도 자동 리셋.
  - Notifier: 텔레그램으로 체결·에러·차단·생존신호 전송(외부 의존 없이 표준 라이브러리).
    미설정이면 조용히 no-op(끔). 알림 실패가 매매를 막지 않도록 모든 예외 흡수.
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Optional


def _now_kst() -> datetime:
    # 컨테이너 TZ 무관하게 KST(UTC+9) 기준 날짜 사용
    from datetime import timezone, timedelta
    return datetime.now(timezone(timedelta(hours=9)))


class Notifier:
    """텔레그램 알림(설정 없으면 no-op). 매매 흐름을 절대 막지 않도록 예외 전부 흡수."""

    def __init__(self, cfg: dict):
        tg = cfg.get("telegram", {})
        self.enabled = bool(tg.get("enabled", False))
        self.token = tg.get("bot_token", "") or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = tg.get("chat_id", "") or os.environ.get("TELEGRAM_CHAT_ID", "")
        self.notify_trades = bool(tg.get("notify_trades", True))
        self.notify_errors = bool(tg.get("notify_errors", True))
        if self.enabled and (not self.token or not self.chat_id):
            self.enabled = False   # 키 없으면 자동 비활성(안전)

    def send(self, text: str) -> bool:
        if not self.enabled:
            return False
        try:
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            data = urllib.parse.urlencode({
                "chat_id": self.chat_id, "text": text,
                "parse_mode": "HTML", "disable_web_page_preview": "true",
            }).encode()
            urllib.request.urlopen(url, data=data, timeout=8)
            return True
        except Exception:
            return False   # 알림 실패는 무시(매매 지속이 우선)


class RiskGuard:
    """포트폴리오 차단기. 합산자산 기준 낙폭·일일손실 한도 초과 시 halt(신규진입 중단)."""

    def __init__(self, cfg: dict, state_path: str, notifier: Optional[Notifier] = None):
        s = cfg.get("safety", {})
        self.max_dd = float(s.get("max_drawdown_pct", 0.15))        # 고점대비 낙폭 한도
        self.max_daily = float(s.get("max_daily_loss_pct", 0.05))   # 일일손실 한도
        self.enabled = bool(s.get("enabled", True))
        self.notifier = notifier
        self.state_path = state_path
        self.peak: Optional[float] = None
        self.day: Optional[str] = None
        self.day_start: Optional[float] = None
        self.halted = False
        self.reason: Optional[str] = None
        self._load()

    def _load(self):
        if os.path.exists(self.state_path):
            try:
                d = json.load(open(self.state_path, encoding="utf-8"))
                self.peak = d.get("peak"); self.day = d.get("day")
                self.day_start = d.get("day_start")
                self.halted = d.get("halted", False); self.reason = d.get("reason")
            except Exception:
                pass

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self.state_path) or ".", exist_ok=True)
            json.dump({"peak": self.peak, "day": self.day, "day_start": self.day_start,
                       "halted": self.halted, "reason": self.reason},
                      open(self.state_path, "w", encoding="utf-8"))
        except Exception:
            pass

    def update(self, equity: float) -> bool:
        """합산자산 갱신 → halt 여부 반환. enabled=False면 항상 False."""
        if not self.enabled or equity <= 0:
            return False
        today = _now_kst().strftime("%Y-%m-%d")
        if self.day != today:                       # 새 날: 일일기준 리셋
            self.day, self.day_start = today, equity
            if self.reason == "daily":              # 일일손실 차단은 새 날에 해제
                self.halted, self.reason = False, None
                if self.notifier:
                    self.notifier.send("🟢 <b>차단 해제</b> — 새 거래일, 신규진입 재개")
        if self.peak is None or equity > self.peak:
            self.peak = equity
        dd = (self.peak - equity) / self.peak if self.peak else 0.0
        daily = (self.day_start - equity) / self.day_start if self.day_start else 0.0

        if not self.halted:
            trip = None
            if dd >= self.max_dd:
                trip = ("drawdown", f"고점대비 낙폭 -{dd*100:.1f}% (한도 -{self.max_dd*100:.0f}%)")
            elif daily >= self.max_daily:
                trip = ("daily", f"당일 손실 -{daily*100:.1f}% (한도 -{self.max_daily*100:.0f}%)")
            if trip:
                self.halted, self.reason = True, trip[0]
                if self.notifier:
                    self.notifier.send(
                        f"🔴 <b>차단기 작동</b> — 신규진입 전면중단\n{trip[1]}\n"
                        f"합산자산 {equity:,.0f}원. 보유분은 각 전략대로 청산 계속.")
        self._save()
        return self.halted

    def status(self) -> dict:
        return {"enabled": self.enabled, "halted": self.halted, "reason": self.reason,
                "peak": self.peak, "max_dd": self.max_dd, "max_daily": self.max_daily}
