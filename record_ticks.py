"""바이낸스+업비트 틱(체결) 실시간 동시 수집 — 진짜 지연(lead-lag) 측정용.

1분봉은 초 단위 지연을 못 봐서, 실제 lead-lag는 '틱(체결)'으로 봐야 한다.
두 거래소 WebSocket을 동시에 물고, 각 체결을 **우리가 받은 정밀 시각(recv_ts)**과 함께 기록.
이후 leadlag_analyze 가 '바이낸스 체결 → 업비트 체결' 시간차를 밀리초 단위로 측정.

연결:
  - 업비트:   wss://api.upbit.com/websocket/v1   (구독 JSON 전송, type=trade)
  - 바이낸스: wss://stream.binance.com:9443/stream?streams=btcusdt@aggTrade/...

기록열: recv_ts(로컬 epoch, 고정밀), exch, coin, price, exch_ts(거래소 체결시각 ms)
  → recv_ts = '우리가 실제로 받은 시각' = 실거래에서 반응 가능한 시점(이게 핵심).

⚠️ 한국망 PC에서 실행(업비트=한국IP). 바이낸스 막히면 알려주세요(대체 안내).
의존성:  pip install websockets        (requirements.txt에 추가됨)

사용법:
    python record_ticks.py                  # BTC,ETH (틱 많음 → 우선 소수 코인 권장)
    python record_ticks.py KRW-BTC KRW-XRP  # 코인 지정
  Ctrl+C 로 종료. 데이터: leadlag_ticks/ticks_<날짜시각>.csv
이후:  git add leadlag_ticks && git commit -m "틱 데이터" && git push
"""
from __future__ import annotations

import asyncio
import csv
import json
import os
import sys
import time
from datetime import datetime

try:
    import websockets
except ImportError:
    print("먼저:  pip install websockets"); sys.exit(1)

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "leadlag_ticks")
UPBIT_WS = "wss://api.upbit.com/websocket/v1"
BINANCE_WS = "wss://stream.binance.com:9443/stream"

SYMBOL = {  # 업비트 코드 → 바이낸스 심볼(소문자)
    "KRW-BTC": "btcusdt", "KRW-ETH": "ethusdt", "KRW-XRP": "xrpusdt",
    "KRW-SOL": "solusdt", "KRW-ADA": "adausdt", "KRW-DOGE": "dogeusdt",
    "KRW-TRX": "trxusdt", "KRW-LINK": "linkusdt", "KRW-AVAX": "avaxusdt",
    "KRW-DOT": "dotusdt",
}

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


async def upbit_stream(coins, write, stop):
    """업비트 체결 스트림. 끊기면 자동 재접속."""
    sub = [{"ticket": "leadlag"}, {"type": "trade", "codes": coins}, {"format": "SIMPLE"}]
    while not stop.is_set():
        try:
            async with websockets.connect(UPBIT_WS, ping_interval=20, max_size=None) as ws:
                await ws.send(json.dumps(sub))
                print(f"[업비트] 접속 — {coins}")
                async for msg in ws:
                    recv = time.time()
                    d = json.loads(msg)
                    # SIMPLE: cd=코드, tp=체결가, ttms=체결시각(ms)
                    write(recv, "upbit", d["cd"], float(d["tp"]), d.get("ttms"))
                    if stop.is_set():
                        break
        except Exception as e:
            if stop.is_set():
                break
            print(f"[업비트] 재접속... ({e})"); await asyncio.sleep(2)


async def binance_stream(coins, write, stop):
    """바이낸스 aggTrade 스트림(코인별). 끊기면 자동 재접속."""
    syms = [SYMBOL[c] for c in coins if c in SYMBOL]
    rev = {SYMBOL[c]: c for c in coins if c in SYMBOL}      # 바이낸스심볼 → 업비트코드
    url = BINANCE_WS + "?streams=" + "/".join(f"{s}@aggTrade" for s in syms)
    while not stop.is_set():
        try:
            async with websockets.connect(url, ping_interval=20, max_size=None) as ws:
                print(f"[바이낸스] 접속 — {syms}")
                async for msg in ws:
                    recv = time.time()
                    d = json.loads(msg).get("data", {})
                    if d.get("e") != "aggTrade":
                        continue
                    coin = rev.get(d["s"].lower(), d["s"])
                    write(recv, "binance", coin, float(d["p"]), d.get("T"))
                    if stop.is_set():
                        break
        except Exception as e:
            if stop.is_set():
                break
            print(f"[바이낸스] 재접속... ({e})"); await asyncio.sleep(2)


async def main():
    args = [a for a in sys.argv[1:] if a.startswith("KRW-")]
    coins = args or ["KRW-BTC", "KRW-ETH"]               # 기본 소수(틱 폭증 방지)
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, f"ticks_{datetime.now():%Y%m%d_%H%M%S}.csv")
    f = open(path, "w", newline="", encoding="utf-8")
    w = csv.writer(f); w.writerow(["recv_ts", "exch", "coin", "price", "exch_ts"])
    cnt = [0]

    def write(recv, exch, coin, price, exch_ts):
        w.writerow([f"{recv:.6f}", exch, coin, price, exch_ts]); cnt[0] += 1
        if cnt[0] % 2000 == 0:
            f.flush(); print(f"  ...{cnt[0]}건 기록")

    stop = asyncio.Event()
    print(f"수집 시작: {coins}  → {os.path.relpath(path, BASE)}\n  (Ctrl+C 종료)\n")
    tasks = [asyncio.create_task(upbit_stream(coins, write, stop)),
             asyncio.create_task(binance_stream(coins, write, stop))]
    try:
        await asyncio.gather(*tasks)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        stop.set()
        for t in tasks:
            t.cancel()
        f.flush(); f.close()
        print(f"\n종료. 총 {cnt[0]}건 → {os.path.relpath(path, BASE)}")
        print("커밋:  git add leadlag_ticks && git commit -m \"틱 데이터\" && git push")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n중단됨.")
