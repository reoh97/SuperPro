# backtest_data/ — 백테스트용 분봉 (커밋 대상)

거래소 API가 막힌 환경(클라우드 세션 등)에서도 실데이터로 백테스트하기 위한 폴더입니다.

## 채우는 법 (거래소 접근 가능한 곳, 예: 한국 IP)

```bash
python fetch_local.py            # 10종목 × bull/sideways/bear → CSV 저장
git add backtest_data && git commit -m "백테스트용 분봉 수집" && git push
```

저장 형식: `backtest_data/<국면>__<티커>.csv` (예: `bear__KRW-BTC.csv`),
컬럼 `open,high,low,close,volume`, 인덱스 = 시각.

## 사용

```bash
python circuit_backtest.py       # 이 폴더의 CSV를 자동으로 우선 사용해 실수치 출력
```

> ⚠️ 합성데이터 CSV는 커밋하지 마세요. 실제 거래소 수집분만 의미 있습니다.
