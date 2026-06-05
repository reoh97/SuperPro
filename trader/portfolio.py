"""다종목 포트폴리오 + 분할매수(스케일인) 백테스터.

아이디어(사용자 전략):
  - 총자본을 N개 종목에 균등 분배(예: 2,000만원 → 10종목 × 200만원).
  - 한 종목을 '통매수'하지 않고 여러 트랜치로 '분할매수'.
  - 박스권 하단이 깨져도 한 방에 무너지지 않게, 떨어질수록 나눠 담아
    평단가를 낮추고 회복 시 평단 대비 익절.

트랜치(추가매수) 규칙 — '둘 혼합':
  - 1번 트랜치: 국면별 진입신호(strategies.evaluate.should_enter)에서 진입.
  - 2~N번 트랜치: (a) 직전 진입가 대비 add_drop_pct 이상 하락 AND
                    (b) 진입신호가 다시 떠야 추가 (무분별 물타기 방지).

청산(평단가 기준):
  - 익절 TP : high >= avg*(1+tp_pct)
  - 방어 SL : 모든 트랜치를 소진한 뒤에만 작동. low <= avg*(1-hard_sl_pct)
             (트랜치가 남아 있는 동안은 손절 대신 분할매수로 대응)
  - 타임아웃: max_hold_bars 초과 시 종가 청산

미래참조 없음: 지표는 인과적, 진입은 현재봉 종가, 청산은 현재봉 고가/저가 감시.
봉내 동시충족은 보수적으로 'SL 먼저' 가정.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pandas as pd

from . import backtest, indicators, regime, strategies


@dataclass
class CoinTrade:
    ticker: str
    regime: str          # 진입 시점 확정 국면(UP/SIDEWAYS/DOWN)
    entry_time: str
    exit_time: str
    tranches: int        # 실제 들어간 분할 횟수
    avg_entry: float     # 최종 평단가
    exit: float
    exit_reason: str     # tp | sl | trail | timeout | eod
    invested: float      # 실제 투입 원금(수수료 포함 전 매수금액 합)
    net_pnl: float       # 수수료 차감 순손익(원)
    ret_pct: float


@dataclass
class CoinResult:
    ticker: str
    budget: float
    end_value: float
    trades: List[CoinTrade] = field(default_factory=list)
    bars: int = 0
    fees_paid: float = 0.0
    error: Optional[str] = None
    # (timestamp, 평가액=현금+보유평가) 시계열. record_curve=True일 때만 채워짐.
    equity_curve: List[tuple] = field(default_factory=list)


def _run_coin(ticker: str, df: pd.DataFrame, cfg: dict,
              record_curve: bool = False) -> CoinResult:
    """한 종목에 대해 국면별 전략 + 분할매수를 시뮬레이션.

    - 상승장(trail): 단일 진입, 트레일링으로 길게 끌어 5~6%+ 포착 → 청산 후 재진입.
    - 횡보/하락장(fixed): 분할매수로 평단을 낮추고 평단 대비 익절(국면별 목표).
                          트랜치를 모두 소진한 뒤에만 방어 손절이 작동.
    """
    fee = float(cfg["trade"]["fee"])
    pcfg = cfg.get("portfolio", {})
    budget = float(pcfg.get("per_coin_krw", 2000000))
    n_tranche = int(pcfg.get("tranches", 5))
    add_drop = float(pcfg.get("add_drop_pct", 0.02))
    add_mode = str(pcfg.get("add_drop_mode", "fixed"))      # fixed | atr (변동성 적응형)
    add_atr_mult = float(pcfg.get("add_drop_atr_mult", 1.5))
    add_min = float(pcfg.get("add_drop_min", 0.008))
    add_max = float(pcfg.get("add_drop_max", 0.05))
    hard_sl = float(pcfg.get("hard_sl_pct", 0.06))
    max_hold = int(pcfg.get("max_hold_bars", 480))
    tranche_krw = budget / n_tranche

    scfg = cfg.get("scalp", {})
    confirm_bars = int(scfg.get("confirm_bars", 3))
    exit_on_down = bool(scfg.get("exit_on_down", True))  # DOWN 확정전환 시 보유분 즉시청산(False=홀드)
    ucfg = scfg.get("uptrend", {})
    pyramid = bool(ucfg.get("pyramid", False))           # 상승장 불타기
    pyramid_step = float(ucfg.get("pyramid_step", 0.02))  # 직전 진입가 +step 상승마다 추가
    warmup = int(cfg.get("indicators", {}).get("ema_slow", 200)) + 5

    d = indicators.enrich(df, cfg)
    rows = d.reset_index()
    time_col = rows.columns[0]

    res = CoinResult(ticker=ticker, budget=budget, end_value=budget, bars=len(rows))
    cash = budget          # 이 종목에 배정된 현금
    pos = None             # 보유 포지션 dict

    confirmed = regime.SIDEWAYS
    streak_reg, streak = None, 0

    def close_pos(p, exit_price, exit_reason, t):
        nonlocal cash
        proceeds = exit_price * p["size"]
        exit_fee = proceeds * fee
        cash += proceeds - exit_fee
        res.fees_paid += exit_fee
        avg = p["cost"] / p["size"]
        net = (proceeds - exit_fee) - (p["cost"] + p["fees"])
        res.trades.append(CoinTrade(
            ticker=ticker, regime=p["regime"], entry_time=p["entry_time"],
            exit_time=t, tranches=p["used"], avg_entry=avg, exit=exit_price,
            exit_reason=exit_reason, invested=p["cost"],
            net_pnl=net, ret_pct=net / p["cost"] if p["cost"] else 0.0,
        ))

    def add_tranche(p, price):
        """포지션에 1트랜치 추가(분할매수/불타기 공용). 평단·수량·사용횟수 갱신."""
        nonlocal cash
        spend = min(tranche_krw, cash)
        if spend <= 0 or price <= 0:
            return
        entry_fee = spend * fee
        p["size"] += (spend - entry_fee) / price
        p["cost"] += spend - entry_fee
        p["fees"] += entry_fee
        p["used"] += 1
        p["last_entry"] = price
        cash -= spend
        res.fees_paid += entry_fee

    for i in range(warmup, len(rows)):
        row = rows.iloc[i]
        prev = rows.iloc[i - 1]
        t = str(row[time_col])

        # 평가액(현금+보유평가) 시계열 — 라이브 서킷이 보는 '총평가액'과 동일 의미.
        # 현재봉 종가로 마크(봉내 체결은 다음봉에 반영=1봉 지연, 빈도추정엔 무방).
        if record_curve:
            px = float(row["close"])
            res.equity_curve.append((t, cash + (pos["size"] * px if pos is not None else 0.0)))

        # 확정 국면 갱신(휩쏘 방지)
        raw = regime.classify(row, cfg)
        if raw == streak_reg:
            streak += 1
        else:
            streak_reg, streak = raw, 1
        if streak >= confirm_bars:
            confirmed = streak_reg

        sig = strategies.evaluate(prev, row, confirmed, cfg, fee)

        if pos is not None:
            pos["bars_held"] += 1
            avg = pos["cost"] / pos["size"]
            exit_price = exit_reason = None

            # 국면전환 탈출: 보유 중 DOWN으로 확정 전환되면 즉시 청산
            # (박스/추세가 진짜로 깨진 신호 → 물타기 멈추고 탈출, 큰 방어손절 회피)
            # exit_on_down=False면 청산하지 않고 각자 TP/SL까지 홀드(약세장 '대기' 비교용).
            if exit_on_down and confirmed == regime.DOWN and pos["regime"] != regime.DOWN:
                close_pos(pos, float(row["close"]), "regime", t)
                pos = None
                continue

            if pos["exit_mode"] == "trail":
                # 상승장: 고점 갱신 → 손절선을 고점 대비로 끌어올림(최초 손절선이 바닥)
                pos["peak"] = max(pos["peak"], float(row["high"]))
                stop = max(pos["sl_price"], pos["peak"] * (1 - pos["trail_pct"]))
                if row["low"] <= stop:
                    exit_price, exit_reason = stop, "trail"
                # 추세 모드는 타임아웃 없이 트레일링이 청산을 책임짐
            elif pos["exit_mode"] == "scalp":
                # 상승장 1% 자주먹기: 단일진입, 고정 익절 + 고정 손절(물타기 없음)
                tp_price = avg * (1 + pos["tp_pct"])
                if row["low"] <= pos["sl_price"]:
                    exit_price, exit_reason = pos["sl_price"], "sl"
                elif row["high"] >= tp_price:
                    exit_price, exit_reason = tp_price, "tp"
                elif pos["bars_held"] >= max_hold:
                    exit_price, exit_reason = float(row["close"]), "timeout"
            else:
                # 횡보/하락장(분할매수): 평단 대비 익절, 트랜치 소진 후 방어 손절
                tp_price = avg * (1 + pos["tp_pct"])
                if pos["used"] >= n_tranche and row["low"] <= avg * (1 - hard_sl):
                    exit_price, exit_reason = avg * (1 - hard_sl), "sl"
                elif row["high"] >= tp_price:
                    exit_price, exit_reason = tp_price, "tp"
                elif pos["bars_held"] >= max_hold:
                    exit_price, exit_reason = float(row["close"]), "timeout"

            if exit_price is not None:
                close_pos(pos, exit_price, exit_reason, t)
                pos = None
                continue

            # 추가매수(분할, fixed 모드만): 직전 진입가 대비 'drop' 이상 하락 + 신호 재발생.
            #   drop = 고정(add_drop) 또는 ATR 적응형(변동성×배수, min/max 클램프).
            if add_mode == "atr" and row["close"] > 0:
                drop = min(max(add_atr_mult * float(row["atr"]) / float(row["close"]), add_min), add_max)
            else:
                drop = add_drop
            if pos["exit_mode"] == "fixed" and pos["used"] < n_tranche \
                    and sig.should_enter and row["close"] <= pos["last_entry"] * (1 - drop):
                add_tranche(pos, float(row["close"]))

            # 불타기(trail 모드): 상승 지속 시 오르는 포지션에 추가(직전가 +pyramid_step↑ & UP)
            elif pos["exit_mode"] == "trail" and pyramid and pos["used"] < n_tranche \
                    and confirmed == regime.UP \
                    and row["close"] >= pos["last_entry"] * (1 + pyramid_step):
                add_tranche(pos, float(row["close"]))
            continue

        # 미보유 → 국면별 신호로 1번 트랜치 진입
        if sig.should_enter:
            spend = min(tranche_krw, cash)
            if spend <= 0:
                continue
            entry_price = float(row["close"])
            entry_fee = spend * fee
            size = (spend - entry_fee) / entry_price
            cash -= spend
            res.fees_paid += entry_fee
            pos = {
                "size": size, "cost": spend - entry_fee, "fees": entry_fee,
                "used": 1, "last_entry": entry_price, "entry_time": t,
                "bars_held": 0, "regime": confirmed,
                "exit_mode": sig.exit_mode, "tp_pct": sig.tp_pct,
                "trail_pct": sig.trail_pct,
                "sl_price": entry_price * (1 - sig.sl_pct), "peak": entry_price,
            }

    # 종료 시 미청산 포지션은 마지막 종가로 평가
    if pos is not None:
        last = rows.iloc[-1]
        close_pos(pos, float(last["close"]), "eod", str(last[time_col]))

    res.end_value = cash
    return res


def run(cfg: dict, unit: int, count: int, to_kst=None, log=print) -> List[CoinResult]:
    """설정의 종목 리스트 전체를 분할매수 전략으로 백테스트.
    to_kst를 주면 그 시점 이전의 과거구간(예: 2024년 상승장)을 검증."""
    pcfg = cfg.get("portfolio", {})
    tickers = pcfg.get("tickers", [cfg["ticker"]])
    results: List[CoinResult] = []
    for tk in tickers:
        log(f"  [{tk}] 분봉 {count}개 수집 중...")
        df = backtest.fetch_minutes(tk, unit, count, to_kst=to_kst)
        if df is None or len(df) < 250:
            results.append(CoinResult(ticker=tk, budget=float(pcfg.get("per_coin_krw", 0)),
                                      end_value=float(pcfg.get("per_coin_krw", 0)),
                                      error="데이터 부족/수집 실패"))
            continue
        results.append(_run_coin(tk, df, cfg))
    return results


def summarize(results: List[CoinResult], cfg: dict) -> str:
    pcfg = cfg.get("portfolio", {})
    total_budget = sum(r.budget for r in results)
    total_end = sum(r.end_value for r in results)
    total_net = total_end - total_budget
    all_trades = [t for r in results for t in r.trades]
    n = len(all_trades)
    wins = [t for t in all_trades if t.net_pnl > 0]
    gross_win = sum(t.net_pnl for t in wins)
    gross_loss = -sum(t.net_pnl for t in all_trades if t.net_pnl <= 0)
    pf = (gross_win / gross_loss) if gross_loss > 0 else float("inf")
    winrate = (len(wins) / n) if n else 0.0
    total_fees = sum(r.fees_paid for r in results)

    L = []
    L.append("=" * 72)
    L.append(f"  포트폴리오 분할매수 백테스트  ({len(results)}종목, {pcfg.get('tranches')}분할)")
    L.append("=" * 72)
    L.append(f"  총 배정자본 : {total_budget:>16,.0f} 원")
    L.append(f"  최종 평가   : {total_end:>16,.0f} 원")
    ret = total_net / total_budget if total_budget else 0.0
    L.append(f"  순손익      : {total_net:>16,.0f} 원  ({ret*100:+.2f}%)")
    L.append(f"  낸 수수료   : {total_fees:>16,.0f} 원")
    L.append("-" * 72)
    L.append(f"  총 거래수   : {n}")
    L.append(f"  승률        : {winrate*100:.1f}%  (승 {len(wins)} / 패 {n-len(wins)})")
    pf_s = "inf" if pf == float("inf") else f"{pf:.2f}"
    L.append(f"  손익비(PF)  : {pf_s}" + ("  (>1 이어야 수익)" if pf != float("inf") else ""))
    L.append("-" * 72)
    L.append("  종목별:")
    L.append(f"    {'종목':<10}{'순손익':>14}{'수익률':>9}{'거래':>5}{'승':>4}{'평균분할':>8}")
    for r in sorted(results, key=lambda x: x.end_value - x.budget, reverse=True):
        net = r.end_value - r.budget
        if r.error:
            L.append(f"    {r.ticker:<10}{'(' + r.error + ')':>30}")
            continue
        tr = r.trades
        w = sum(1 for t in tr if t.net_pnl > 0)
        avg_tr = (sum(t.tranches for t in tr) / len(tr)) if tr else 0.0
        rp = net / r.budget * 100 if r.budget else 0.0
        L.append(f"    {r.ticker:<10}{net:>14,.0f}{rp:>8.1f}%{len(tr):>5}{w:>4}{avg_tr:>8.1f}")
    # 국면별 분해(상승/횡보/하락 각각 수익 나는지)
    L.append("-" * 72)
    L.append("  국면별:")
    by_reg: Dict[str, List[CoinTrade]] = {}
    for t in all_trades:
        by_reg.setdefault(t.regime, []).append(t)
    for reg in (regime.UP, regime.SIDEWAYS, regime.DOWN):
        ts = by_reg.get(reg, [])
        if not ts:
            continue
        net = sum(t.net_pnl for t in ts)
        w = sum(1 for t in ts if t.net_pnl > 0)
        avg_ret = sum(t.ret_pct for t in ts) / len(ts) * 100
        L.append(f"    {reg:<9}{net:>14,.0f} 원  ({len(ts)}건, 승 {w}, 평균 {avg_ret:+.2f}%)")
    # 청산 사유 분포
    reasons: Dict[str, int] = {}
    for t in all_trades:
        reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1
    L.append("-" * 72)
    L.append(f"  청산사유    : {reasons}")
    L.append("=" * 72)
    if total_net <= 0:
        L.append("  ⚠️ 순손익 0 이하 — 분할/익절 파라미터 또는 진입 로직 조정 필요.")
    else:
        L.append("  ✅ 수수료 차감 후 플러스 — 과거 성과가 미래를 보장하진 않음.")
    return "\n".join(L)
