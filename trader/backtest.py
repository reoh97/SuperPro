"""분봉 백테스터: 국면판단 + 국면별 전략을 과거 데이터로 검증.

핵심:
  - 수수료(왕복)를 차감한 '순수익' 기준으로 평가 → 수수료를 이기는지 확인
  - 미래참조 없음: 지표는 인과적, 진입은 현재 봉 종가, 청산은 다음 봉부터 SL/TP 감시
  - 봉내(intrabar) SL/TP 동시 충족 시 보수적으로 'SL 먼저' 가정
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import List, Optional

import pandas as pd
import pyupbit

from . import indicators, regime, strategies


# ---------- 과거 분봉 수집 (200개 초과는 페이지네이션) ----------
def fetch_minutes(ticker: str, unit: int, count: int) -> Optional[pd.DataFrame]:
    interval = f"minute{unit}"
    frames: List[pd.DataFrame] = []
    to = None
    remaining = count
    while remaining > 0:
        n = min(200, remaining)
        # 페이지별 재시도 (레이트리밋 시 None 반환될 수 있음)
        df = None
        for attempt in range(4):
            df = pyupbit.get_ohlcv(ticker, interval=interval, count=n, to=to)
            if df is not None and len(df) > 0:
                break
            time.sleep(0.4 * (attempt + 1))
        if df is None or len(df) == 0:
            break
        frames.append(df)
        # pyupbit는 `to`를 UTC로 해석하므로 KST 인덱스를 9시간 빼서(=UTC) 넘긴다.
        # 그래야 다음 페이지가 '더 과거' 구간을 반환한다.
        to = (df.index[0] - pd.Timedelta(hours=9)).strftime("%Y-%m-%d %H:%M:%S")
        remaining -= len(df)
        if len(df) < n:
            break
        time.sleep(0.25)  # 레이트리밋 보호
    if not frames:
        return None
    out = pd.concat(frames).sort_index()
    out = out[~out.index.duplicated(keep="first")]
    return out


# ---------- 백테스트 ----------
@dataclass
class Trade:
    entry_time: str
    exit_time: str
    regime: str
    entry: float
    exit: float
    reason: str       # 진입 사유
    exit_reason: str  # tp | sl | regime | timeout | eod
    net_pnl: float    # 수수료 차감 순손익(원)
    ret_pct: float    # 순수익률


@dataclass
class Result:
    trades: List[Trade] = field(default_factory=list)
    start_equity: float = 0.0
    end_equity: float = 0.0
    fees_paid: float = 0.0
    bars: int = 0


def run(df: pd.DataFrame, cfg: dict) -> Result:
    fee = float(cfg["trade"]["fee"])
    invest_ratio = float(cfg["trade"]["invest_ratio"])
    scfg = cfg.get("scalp", {})
    max_hold = int(scfg.get("max_hold_bars", 60))
    warmup = int(cfg.get("indicators", {}).get("ema_slow", 200)) + 5

    d = indicators.enrich(df, cfg)
    equity = float(cfg["paper"]["initial_krw"])
    res = Result(start_equity=equity)

    pos = None  # 보유 포지션 dict

    rows = d.reset_index()
    time_col = rows.columns[0]
    res.bars = len(rows)

    # 국면 휩쏘 방지: raw 국면이 confirm_bars 연속 같을 때만 '확정 국면'으로 인정
    confirm_bars = int(scfg.get("confirm_bars", 3))
    confirmed = regime.SIDEWAYS
    streak_reg, streak = None, 0

    for i in range(warmup, len(rows)):
        row = rows.iloc[i]
        prev = rows.iloc[i - 1]
        t = str(row[time_col])

        # 확정 국면 갱신
        raw = regime.classify(row, cfg)
        if raw == streak_reg:
            streak += 1
        else:
            streak_reg, streak = raw, 1
        if streak >= confirm_bars:
            confirmed = streak_reg

        if pos is not None:
            pos["bars_held"] += 1
            exit_price = None
            exit_reason = None

            if pos["exit_mode"] == "trail":
                # 트레일링: 고점 갱신 → 손절선을 고점 대비로 끌어올림(최초 손절선이 바닥)
                pos["peak"] = max(pos["peak"], row["high"])
                trail_stop = pos["peak"] * (1 - pos["trail_pct"])
                stop = max(pos["sl_price"], trail_stop)
                if row["low"] <= stop:
                    exit_price, exit_reason = stop, "trail"
                # 추세 모드는 timeout 없이 트레일링이 청산을 책임짐
            else:
                # 고정 모드(횡보): SL 먼저(보수적), 그다음 TP
                if row["low"] <= pos["sl_price"]:
                    exit_price, exit_reason = pos["sl_price"], "sl"
                elif row["high"] >= pos["tp_price"]:
                    exit_price, exit_reason = pos["tp_price"], "tp"
                elif pos["bars_held"] >= max_hold:
                    exit_price, exit_reason = row["close"], "timeout"

            if exit_price is not None:
                proceeds = exit_price * pos["size"]
                exit_fee = proceeds * fee
                equity += proceeds - exit_fee
                res.fees_paid += exit_fee
                cost = pos["entry"] * pos["size"]
                net = proceeds - exit_fee - (cost + pos["entry_fee"])
                res.trades.append(Trade(
                    entry_time=pos["entry_time"], exit_time=t, regime=pos["regime"],
                    entry=pos["entry"], exit=exit_price, reason=pos["reason"],
                    exit_reason=exit_reason, net_pnl=net,
                    ret_pct=net / (cost + pos["entry_fee"]),
                ))
                pos = None
            continue

        # 미보유 → 확정 국면 기준 진입 신호
        sig = strategies.evaluate(prev, row, confirmed, cfg, fee)
        if sig.should_enter:
            spend = equity * invest_ratio
            entry_price = float(row["close"])
            entry_fee = spend * fee
            size = (spend - entry_fee) / entry_price
            equity -= spend
            res.fees_paid += entry_fee
            pos = {
                "entry": entry_price, "size": size,
                "exit_mode": sig.exit_mode,
                "tp_price": entry_price * (1 + sig.tp_pct),
                "sl_price": entry_price * (1 - sig.sl_pct),
                "trail_pct": sig.trail_pct, "peak": entry_price,
                "regime": confirmed, "reason": sig.reason,
                "entry_time": t, "entry_fee": entry_fee, "bars_held": 0,
            }

    # 종료 시 미청산 포지션은 마지막 종가로 평가
    if pos is not None:
        last = rows.iloc[-1]
        proceeds = float(last["close"]) * pos["size"]
        exit_fee = proceeds * fee
        equity += proceeds - exit_fee
        res.fees_paid += exit_fee
        cost = pos["entry"] * pos["size"]
        net = proceeds - exit_fee - (cost + pos["entry_fee"])
        res.trades.append(Trade(
            entry_time=pos["entry_time"], exit_time=str(last[time_col]),
            regime=pos["regime"], entry=pos["entry"], exit=float(last["close"]),
            reason=pos["reason"], exit_reason="eod", net_pnl=net,
            ret_pct=net / (cost + pos["entry_fee"]),
        ))

    res.end_equity = equity
    return res


def summarize(res: Result, cfg: dict) -> str:
    trades = res.trades
    n = len(trades)
    wins = [t for t in trades if t.net_pnl > 0]
    losses = [t for t in trades if t.net_pnl <= 0]
    gross_win = sum(t.net_pnl for t in wins)
    gross_loss = -sum(t.net_pnl for t in losses)
    total = res.end_equity - res.start_equity
    ret = total / res.start_equity if res.start_equity else 0.0
    winrate = (len(wins) / n) if n else 0.0
    pf = (gross_win / gross_loss) if gross_loss > 0 else float("inf")

    # 국면별 분해
    by_reg = {}
    for t in trades:
        by_reg.setdefault(t.regime, []).append(t.net_pnl)

    lines = []
    lines.append("=" * 56)
    lines.append(f"  백테스트 결과  ({cfg['ticker']}, {res.bars}봉)")
    lines.append("=" * 56)
    lines.append(f"  시작자본    : {res.start_equity:>14,.0f} 원")
    lines.append(f"  최종자산    : {res.end_equity:>14,.0f} 원")
    lines.append(f"  순손익      : {total:>14,.0f} 원  ({ret*100:+.2f}%)")
    lines.append(f"  낸 수수료   : {res.fees_paid:>14,.0f} 원")
    lines.append("-" * 56)
    lines.append(f"  총 거래수   : {n}")
    lines.append(f"  승률        : {winrate*100:.1f}%  (승 {len(wins)} / 패 {len(losses)})")
    lines.append(f"  손익비(PF)  : {pf:.2f}" + ("  (>1 이어야 수익)" if pf != float('inf') else ""))
    if wins:
        lines.append(f"  평균수익    : {gross_win/len(wins):>14,.0f} 원")
    if losses:
        lines.append(f"  평균손실    : {-gross_loss/len(losses):>14,.0f} 원")
    lines.append("-" * 56)
    lines.append("  국면별 순손익:")
    for reg, pnls in by_reg.items():
        lines.append(f"    {reg:<9}: {sum(pnls):>12,.0f} 원  ({len(pnls)}건)")
    # 청산 사유 분포
    reasons = {}
    for t in trades:
        reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1
    lines.append(f"  청산사유    : {reasons}")
    lines.append("=" * 56)
    if total <= 0:
        lines.append("  ⚠️ 순손익이 0 이하 — 이 설정으론 수수료를 이기지 못함.")
        lines.append("     파라미터(TP/SL·지표·타임프레임) 조정 또는 전략 보완 필요.")
    else:
        lines.append("  ✅ 수수료 차감 후에도 플러스 — 단, 과거 성과가 미래를 보장하진 않음.")
    return "\n".join(lines)
