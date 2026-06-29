"""번 돈 적립 (ProfitSkim) — 고점(HWM) 기준 수익 절반 적립 → 현금흐름(C 목표).

원리: 합산자산이 *새 최고점*을 찍을 때만, 새로 번 부분의 skim_ratio(기본 50%)를 '적립금'으로.
  - 고점 기준이라 약세장에 자산 줄면 적립 0 → **원금 절대 안 까먹음**.
  - 절반만 빼고 절반은 굴림(복리) → 원금도 천천히 성장.
  - 적립금 = 인출 가능액(빼 쓸 돈). 들쭉날쭉(불장 펑펑/약세 가뭄)은 사용자가 버퍼로 평탄화.

V1=추적·표시(매매는 전체 자본 계속, 적립금은 '얼마 빼도 되는지' 마커). 인출은 수동.
상태 영속 → 재시작해도 적립금·기준점 유지.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Optional


class ProfitSkim:
    def __init__(self, cfg: dict, state_path: str, notifier=None):
        s = cfg.get("skim", {})
        self.enabled = bool(s.get("enabled", False))
        self.ratio = float(s.get("ratio", 0.5))                 # 적립 비율(번 것의 50%)
        self.principal = float(s.get("principal_krw", 20_000_000))  # 원금 기준(시작 기준점)
        self.state_path = state_path
        self.notifier = notifier
        self.baseline = self.principal      # 이 위로 새로 벌면 절반 적립, 적립 후 기준점 상승
        self.reserve = 0.0                  # 누적 적립금(인출 가능)
        self.peak_equity = self.principal
        self.last_skim_at: Optional[str] = None
        self._load()

    def _load(self):
        if os.path.exists(self.state_path):
            try:
                d = json.load(open(self.state_path, encoding="utf-8"))
                self.baseline = d.get("baseline", self.principal)
                self.reserve = d.get("reserve", 0.0)
                self.peak_equity = d.get("peak_equity", self.principal)
                self.last_skim_at = d.get("last_skim_at")
            except Exception:
                pass

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self.state_path) or ".", exist_ok=True)
            json.dump({"baseline": self.baseline, "reserve": self.reserve,
                       "peak_equity": self.peak_equity, "last_skim_at": self.last_skim_at},
                      open(self.state_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        except Exception:
            pass

    def update(self, equity: float) -> dict:
        """합산자산 갱신 → 새 고점이면 초과분의 ratio 적립. 반환: 현황."""
        if not self.enabled or equity <= 0:
            return self.status()
        self.peak_equity = max(self.peak_equity, equity)
        # 새 고점(기준점 초과)일 때만 적립 → 약세장(기준점 이하)엔 적립 0(원금보호)
        if equity > self.baseline:
            new_profit = equity - self.baseline
            skim = new_profit * self.ratio
            self.reserve += skim
            self.baseline = equity              # 새 고점=기준점(같은 고점 반복적립 방지). 절반은 자본에 남아 복리

            self.last_skim_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if self.notifier:
                self.notifier.send(f"💰 <b>적립</b> +{skim:,.0f}원 → 인출가능 누적 {self.reserve:,.0f}원 "
                                   f"(합산자산 {equity:,.0f})")
            self._save()
        return self.status()

    def withdraw(self, amount: float) -> float:
        """적립금에서 인출(수동). 실제 출금은 거래소에서, 여기선 장부만 차감."""
        amt = min(amount, self.reserve)
        self.reserve -= amt
        self._save()
        return amt

    def status(self) -> dict:
        return {"enabled": self.enabled, "reserve": self.reserve, "baseline": self.baseline,
                "peak_equity": self.peak_equity, "ratio": self.ratio,
                "last_skim_at": self.last_skim_at}
