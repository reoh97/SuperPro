"""AI 종합 판단 (Claude).

차트가 변동성 돌파 매수 신호를 냈을 때, 영문 코인 뉴스를 함께 읽고
호재/악재를 종합하여 '매수(BUY) vs 보류(HOLD)'를 최종 결정합니다.

비용 제어:
  - 차트가 매수 신호를 낼 때만 호출 (매 틱마다 호출하지 않음)
  - min_interval_sec 만큼의 호출 쿨다운
  - 저렴한 모델(기본 Haiku) + 구조화 출력으로 토큰 최소화
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List, Optional

from pydantic import BaseModel, Field

from .news import Headline

# AI가 따라야 할 지시문(고정). 안정적인 prefix 이므로 prompt caching 대상으로 표시.
SYSTEM_PROMPT = """당신은 신중한 암호화폐 트레이딩 보조 AI입니다.

상황: 기술적 분석(변동성 돌파 전략)이 이미 '매수 신호'를 냈습니다.
당신의 역할은 최신 영문 코인 뉴스를 읽고, 지금 매수를 진행해도 좋은지(BUY)
아니면 보류해야 하는지(HOLD)를 최종 판단하는 것입니다.

판단 원칙:
- 명백한 악재(거래소 해킹·파산, 강한 규제/소송, 대규모 청산·폭락 우려,
  스테이블코인 디페그, 주요 프로젝트 사고 등)가 보이면 HOLD.
- 명확한 악재가 없고 중립적이거나 호재라면, 차트의 매수 신호를 존중하여 BUY 가능.
- 뉴스가 해당 코인과 무관하거나 정보가 부족하면 차트 신호를 따르되 확신도를 낮춰라.
- 확신이 서지 않으면 보수적으로(HOLD 쪽으로) 판단하라.

반드시 한국어로 간결한 이유를 작성하라."""


class Decision(BaseModel):
    """AI의 매매 판단 (구조화 출력)."""
    action: str = Field(description='"BUY" 또는 "HOLD"')
    confidence: int = Field(description="결정에 대한 확신도 0~100")
    reason: str = Field(description="한국어로 1~2문장의 판단 근거")


# 시장 국면 판단용 지시문 (뉴스 + 차트 종합 → 상승/횡보/하락 결정)
REGIME_SYSTEM_PROMPT = """당신은 암호화폐 시장의 '장세(국면)'를 판단하는 분석 AI입니다.

당신의 역할: 비트코인(시장 대표) 차트 요약과 최신 영문 코인 뉴스를 종합하여,
지금 전체 시장이 어떤 국면인지 결정합니다.

국면 정의:
- BULL(상승장): 가격이 추세적으로 상승, 호재 우위, 위험선호. → 추세추종 전략 적합.
- SIDEWAYS(횡보장): 뚜렷한 방향 없이 등락 반복, 뉴스 중립. → 박스권 단타 적합.
- BEAR(하락장): 추세적 하락, 악재 우위, 위험회피. → 신규매수 자제, 현금 보존.

risk_off 플래그: 거래소 해킹/파산, 강한 규제·소송, 스테이블코인 디페그, 대규모
청산·급락 등 '명백하고 급박한 악재'가 있으면 true (국면과 별개로 즉시 방어).

원칙:
- 차트와 뉴스가 일치하면 확신도를 높여라.
- 차트는 상승인데 뉴스가 강한 악재면 보수적으로(SIDEWAYS/BEAR 쪽) 판단하라.
- 정보가 부족하면 차트를 따르되 확신도를 낮춰라.
반드시 한국어로 간결한 이유를 작성하라."""


class RegimeDecision(BaseModel):
    """AI의 시장 국면 판단 (구조화 출력)."""
    regime: str = Field(description='"BULL"(상승) | "SIDEWAYS"(횡보) | "BEAR"(하락)')
    risk_off: bool = Field(description="명백·급박한 악재로 신규매수를 즉시 중단해야 하면 true")
    confidence: int = Field(description="판단 확신도 0~100")
    reason: str = Field(description="한국어로 1~2문장의 근거(차트·뉴스 종합)")


@dataclass
class AdvisorResult:
    action: str           # BUY | HOLD | ERROR
    confidence: int
    reason: str


@dataclass
class RegimeView:
    regime: str           # BULL | SIDEWAYS | BEAR | ERROR
    risk_off: bool
    confidence: int
    reason: str


class AIAdvisor:
    def __init__(self, cfg: dict):
        ai = cfg.get("ai", {})
        self.enabled = bool(ai.get("enabled", False))
        self.model = ai.get("model", "claude-haiku-4-5")
        self.min_confidence = int(ai.get("min_confidence", 60))
        self.disabled_reason: Optional[str] = None
        self._client = None

        if not self.enabled:
            self.disabled_reason = "config에서 AI 비활성화됨 (ai.enabled=false)"
            return

        key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            self.enabled = False
            self.disabled_reason = "ANTHROPIC_API_KEY 미설정 → 차트 신호만으로 매매"
            return

        try:
            import anthropic
            self._client = anthropic.Anthropic(api_key=key)
        except Exception as e:
            self.enabled = False
            self.disabled_reason = f"anthropic SDK 초기화 실패: {e}"

    def decide(self, ticker: str, signal, price: float,
               headlines: List[Headline]) -> AdvisorResult:
        """매수 신호 상황에서 AI에게 최종 결정을 요청."""
        if not self.enabled or self._client is None:
            return AdvisorResult("ERROR", 0, self.disabled_reason or "AI 비활성")

        news_text = "\n".join(
            f"- [{h.source}] {h.title}" + (f" — {h.summary}" if h.summary else "")
            for h in headlines
        ) or "(수집된 뉴스 없음)"

        user_prompt = f"""[차트 상황]
- 코인: {ticker}
- 현재가: {price:,.0f}원
- 돌파 목표가: {signal.target_price:,.0f}원 (현재가가 이 선을 넘어 매수 신호 발생)
- 전일 변동폭: {signal.range_value:,.0f}원
- 이동평균 필터 통과: {signal.ma_ok}

[최신 영문 코인 뉴스 헤드라인]
{news_text}

위 정보를 종합하여 지금 {ticker} 매수를 진행할지(BUY) 보류할지(HOLD) 판단하세요."""

        try:
            resp = self._client.messages.parse(
                model=self.model,
                max_tokens=600,
                system=[{
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},  # 지시문 캐싱(반복 호출 시 비용 절감)
                }],
                messages=[{"role": "user", "content": user_prompt}],
                output_format=Decision,
            )
            d = resp.parsed_output
            if d is None:
                return AdvisorResult("ERROR", 0, "AI 응답 파싱 실패")
            action = "BUY" if str(d.action).upper().startswith("B") else "HOLD"
            conf = max(0, min(100, int(d.confidence)))
            return AdvisorResult(action, conf, d.reason.strip())
        except Exception as e:
            # API 오류 시 안전하게 보류(HOLD) 처리하고 사유 노출
            return AdvisorResult("ERROR", 0, f"AI 호출 오류: {type(e).__name__}: {e}")

    def approves_buy(self, result: AdvisorResult) -> bool:
        """AI 결과가 매수를 승인하는지 (BUY + 확신도 충족)."""
        return result.action == "BUY" and result.confidence >= self.min_confidence

    def decide_regime(self, market_summary: str,
                      headlines: List[Headline]) -> RegimeView:
        """시장 차트 요약 + 뉴스를 종합해 현재 시장 국면(BULL/SIDEWAYS/BEAR)을 판단."""
        if not self.enabled or self._client is None:
            return RegimeView("ERROR", False, 0, self.disabled_reason or "AI 비활성")

        news_text = "\n".join(
            f"- [{h.source}] {h.title}" + (f" — {h.summary}" if h.summary else "")
            for h in headlines
        ) or "(수집된 뉴스 없음)"

        user_prompt = f"""[시장(비트코인) 차트 요약]
{market_summary}

[최신 영문 코인 뉴스 헤드라인]
{news_text}

위 차트와 뉴스를 종합하여 현재 시장 국면(BULL/SIDEWAYS/BEAR)과 risk_off 여부를 판단하세요."""

        try:
            resp = self._client.messages.parse(
                model=self.model,
                max_tokens=600,
                system=[{
                    "type": "text",
                    "text": REGIME_SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }],
                messages=[{"role": "user", "content": user_prompt}],
                output_format=RegimeDecision,
            )
            d = resp.parsed_output
            if d is None:
                return RegimeView("ERROR", False, 0, "AI 응답 파싱 실패")
            reg = str(d.regime).upper()
            if reg not in ("BULL", "SIDEWAYS", "BEAR"):
                reg = "SIDEWAYS"
            conf = max(0, min(100, int(d.confidence)))
            return RegimeView(reg, bool(d.risk_off), conf, d.reason.strip())
        except Exception as e:
            return RegimeView("ERROR", False, 0, f"AI 호출 오류: {type(e).__name__}: {e}")
