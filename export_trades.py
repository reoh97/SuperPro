"""거래기록 내보내기 — 봇 상태파일(data/*.json)에서 전 엔진 체결내역을 뽑아
reports/trades.csv + reports/summary.txt 로 저장. git으로 올리면 원격에서 분석 가능.

사용(서버에서):
    ./venv/bin/python export_trades.py && git add reports && git commit -m "기록" && git push
그럼 여기서 git pull 받아 reports/trades.csv 로 승률·코인별·사유별 정밀분석.
"""
from __future__ import annotations
import csv, glob, json, os
from datetime import datetime, timezone, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
OUT = os.path.join(BASE, "reports")
ENG = {"sideways": "횡보", "capitulation": "폭락", "longterm": "코어"}


def load_trades():
    rows = []
    for path in glob.glob(os.path.join(DATA, "*_paper.json")):
        name = os.path.basename(path)
        eng = next((v for k, v in ENG.items() if k in name), name)
        try:
            d = json.load(open(path, encoding="utf-8"))
        except Exception:
            continue
        if isinstance(d.get("trades"), list):            # 횡보/폭락: 평탄 trades
            for t in d["trades"]:
                rows.append({**t, "engine": eng})
        if isinstance(d.get("coins"), dict):             # 코어: 코인별 trades
            for tk, c in d["coins"].items():
                for t in (c.get("trades") or []):
                    rows.append({**t, "engine": eng, "ticker": t.get("ticker", tk)})
    rows.sort(key=lambda r: r.get("time", ""))
    return rows


def main():
    os.makedirs(OUT, exist_ok=True)
    rows = load_trades()
    cols = ["time", "engine", "ticker", "side", "amount", "price", "size", "fee", "pnl", "reason"]
    with open(os.path.join(OUT, "trades.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in cols})

    # 요약
    sells = [r for r in rows if r.get("side") == "sell"]
    buys = [r for r in rows if r.get("side") == "buy"]
    lines = []
    kst = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M")
    lines.append(f"# SuperPro 거래요약 (생성 {kst} KST)")
    lines.append(f"총 체결 {len(rows)}건 (매수 {len(buys)} / 매도 {len(sells)})")
    if sells:
        pnls = [float(r.get("pnl") or 0) for r in sells]
        wins = [p for p in pnls if p > 0]
        fees = [float(r.get("fee") or 0) for r in sells]
        lines.append(f"실현손익 합계 {sum(pnls):+,.0f}원  (승 {len(wins)}/{len(sells)} = 승률 {len(wins)/len(sells)*100:.0f}%)")
        lines.append(f"매도 수수료 합계(왕복) {sum(fees):,.0f}원")
        # 엔진별
        lines.append("\n[엔진별]")
        for eng in ("코어", "폭락", "횡보"):
            es = [r for r in sells if r.get("engine") == eng]
            if es:
                p = sum(float(r.get("pnl") or 0) for r in es)
                w = sum(1 for r in es if float(r.get("pnl") or 0) > 0)
                lines.append(f"  {eng}: {len(es)}건 손익 {p:+,.0f}원 승률 {w/len(es)*100:.0f}%")
        # 사유별
        lines.append("\n[청산사유별]")
        reasons = {}
        for r in sells:
            k = r.get("reason") or "?"
            reasons.setdefault(k, []).append(float(r.get("pnl") or 0))
        for k, ps in sorted(reasons.items(), key=lambda x: -len(x[1])):
            w = sum(1 for p in ps if p > 0)
            lines.append(f"  {k}: {len(ps)}건 손익 {sum(ps):+,.0f}원 승률 {w/len(ps)*100:.0f}%")
        # 코인별
        lines.append("\n[코인별]")
        coins = {}
        for r in sells:
            k = (r.get("ticker") or "?").replace("KRW-", "")
            coins.setdefault(k, []).append(float(r.get("pnl") or 0))
        for k, ps in sorted(coins.items(), key=lambda x: sum(x[1])):
            lines.append(f"  {k}: {len(ps)}건 손익 {sum(ps):+,.0f}원")
    txt = "\n".join(lines)
    open(os.path.join(OUT, "summary.txt"), "w", encoding="utf-8").write(txt + "\n")
    print(txt)
    print(f"\n→ reports/trades.csv ({len(rows)}건), reports/summary.txt 저장")
    print("올리기:  git add reports && git commit -m \"기록\" && git push")


if __name__ == "__main__":
    main()
