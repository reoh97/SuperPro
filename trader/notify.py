"""텔레그램 알림 (의존성 없이 표준 라이브러리만 사용).

목표 수익 도달 등 중요한 이벤트를 텔레그램 메시지로 보냅니다.
봇 토큰/채팅ID는 보안을 위해 환경변수(.env) 권장:
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
config.yaml 의 telegram.bot_token / telegram.chat_id 로도 지정 가능(우선순위 낮음).

봇 만들기: 텔레그램에서 @BotFather 로 봇 생성 → 토큰 발급.
채팅ID: 봇과 대화 시작 후 https://api.telegram.org/bot<토큰>/getUpdates 의 chat.id.
"""
from __future__ import annotations

import json
import os
import threading
import urllib.parse
import urllib.request
from typing import Optional


class TelegramNotifier:
    API = "https://api.telegram.org/bot{token}/sendMessage"

    def __init__(self, cfg: dict):
        tg = cfg.get("telegram", {}) or {}
        self.want = bool(tg.get("enabled", True))
        # 환경변수 우선(.env), 없으면 config 값.
        self.token = os.environ.get("TELEGRAM_BOT_TOKEN", tg.get("bot_token", "") or "").strip()
        self.chat_id = os.environ.get("TELEGRAM_CHAT_ID", str(tg.get("chat_id", "") or "")).strip()
        self.timeout = float(tg.get("timeout_sec", 10))

    @property
    def enabled(self) -> bool:
        return self.want and bool(self.token) and bool(self.chat_id)

    @property
    def disabled_reason(self) -> Optional[str]:
        if not self.want:
            return "telegram.enabled=false"
        if not self.token:
            return "TELEGRAM_BOT_TOKEN 없음"
        if not self.chat_id:
            return "TELEGRAM_CHAT_ID 없음"
        return None

    def send(self, text: str) -> None:
        """메시지를 비동기(데몬 스레드)로 전송. 매매 루프를 막지 않도록 best-effort."""
        if not self.enabled:
            return
        t = threading.Thread(target=self._send_blocking, args=(text,), daemon=True)
        t.start()

    def _send_blocking(self, text: str) -> None:
        try:
            url = self.API.format(token=self.token)
            data = urllib.parse.urlencode({
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": "true",
            }).encode("utf-8")
            req = urllib.request.Request(url, data=data, method="POST")
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                resp.read()
        except Exception:
            # 알림 실패가 매매를 방해하면 안 되므로 조용히 무시.
            pass
