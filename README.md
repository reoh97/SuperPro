# 업비트 자동매매 (변동성 돌파 전략)

Python으로 만든 업비트 자동매매 봇 + 웹 대시보드입니다.
**모의(시뮬레이션) 모드를 기본**으로 시작하여 안전하게 전략을 검증한 뒤,
설정 한 줄만 바꿔 실거래로 전환할 수 있습니다.

## 전략: 변동성 돌파 (Larry Williams)

- 전일 변동폭 `range = 전일고가 - 전일저가`
- 목표가 `target = 당일시가 + range × k` (기본 k=0.5)
- 현재가가 목표가를 **돌파하면 매수**, 다음 날 시가에 **매도(청산)**
- (옵션) 이동평균 필터: 현재가가 MA 위일 때만 매수 → 하락장 진입 억제

## 설치

```powershell
# 1) (최초 1회) 가상환경 생성 권장
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2) 의존성 설치
pip install -r requirements.txt
```

## 실행

```powershell
python run.py
```

실행 후 브라우저에서 **http://127.0.0.1:8000** 접속.
- 시작 시에는 안전을 위해 **매매가 정지** 상태입니다.
- 대시보드 [매매 시작] 버튼을 눌러야 실제 평가/주문 루프가 동작합니다.
- 모의 모드는 API 키 없이도 실시간 시세로 동작합니다.

## 설정 (`config.yaml`)

| 항목 | 설명 |
|------|------|
| `mode` | `paper`(모의) 또는 `live`(실거래) |
| `ticker` | 거래 마켓 (예: `KRW-BTC`) |
| `strategy.k` | 변동성 돌파 계수 (0~1, 보통 0.5) |
| `strategy.ma_period` | 이동평균 필터 기간 (0이면 미사용) |
| `trade.invest_ratio` | 매수 시 가용 원화 사용 비중 (0~1) |
| `trade.fee` | 수수료 (업비트 원화마켓 0.0005) |
| `loop.interval_sec` | 현재가 폴링 주기(초) |
| `paper.initial_krw` | 모의 시작 자본 |

## 목표수익 알림 (서킷브레이커)

무지성 반복매매를 막기 위해, **누적 수익률이 목표(기본 +1.1%)에 도달하면
보유 전량을 청산하고 매매를 멈춘 뒤 텔레그램으로 알립니다.** 대시보드 [재개] 버튼을
누르면 그때 평가액을 새 기준선으로 다시 시작합니다. (`config.yaml`의 `circuit` 섹션)

텔레그램 알림을 받으려면 환경변수(.env 권장)를 설정하세요:
```powershell
$env:TELEGRAM_BOT_TOKEN = "BotFather에서_발급받은_토큰"
$env:TELEGRAM_CHAT_ID   = "본인_채팅ID"   # 봇과 대화 후 getUpdates 의 chat.id
```
미설정이면 대시보드 상단 배너로만 알립니다(매매는 동일하게 정지).

## 실거래 전환 (충분히 검증한 뒤에!)

1. [업비트 OpenAPI](https://upbit.com/mypage/open_api_management)에서 키 발급
   (자산조회·주문 권한, **출금 권한은 절대 켜지 마세요**, 가능하면 IP 화이트리스트 설정).
2. 환경변수로 키 등록 (config에 직접 적는 것보다 안전):
   ```powershell
   $env:UPBIT_ACCESS_KEY = "발급받은_액세스_키"
   $env:UPBIT_SECRET_KEY = "발급받은_시크릿_키"
   ```
3. `config.yaml`에서 `mode: live` 로 변경 후 실행.

## 구조

```
run.py              진입점 (엔진 + 웹 대시보드)
config.yaml         설정
trader/
  data.py           시세 조회
  strategy.py       변동성 돌파 전략
  broker.py         모의/실거래 주문
  state.py          잔고·거래내역 (data/state_*.json 에 저장)
  engine.py         매매 루프
web/
  app.py            FastAPI API
  index.html        대시보드 화면
```

## 주의

- 자동매매는 **실제 손실 위험**이 있습니다. 투자 책임은 본인에게 있습니다.
- 반드시 모의 모드로 충분히 검증하고, 실거래는 잃어도 되는 소액으로 시작하세요.
- 이 프로그램은 교육/개인용 예제이며 수익을 보장하지 않습니다.
