# 실전 배포 가이드 (24시간 운영)

봇은 켜둔 컴퓨터에서만 돈다. 끄면 매매 정지(상태는 저장돼 재시작 시 이어감).
24시간 돌리려면 **항상 켜둘 한국 IP 기기**가 필요하다. ⚠️ 업비트는 한국 IP에서만 응답.

## 🚀 한국 VPS 빠른시작 (복붙용)

### 0) VPS 고르기 (한국 IP 필수)
- **무료**: 오라클 클라우드 무료티어 → **춘천 리전** → Ubuntu 22.04 (ARM Ampere). 카드등록 필요·물량 변동.
- **간단·유료**: 카페24 가상서버호스팅 (Ubuntu, 월 ~11,000원). 국내업체라 안정.
- ⚠️ AWS·Vultr 등 **해외 클라우드는 업비트가 IP 차단** 가능 → 한국 국내 리전만.

### 1) SSH 접속 후 설치
```bash
ssh ubuntu@<서버IP>                      # 제공사가 준 IP/계정
sudo apt update && sudo apt install -y python3-pip git
git clone https://github.com/reoh97/superpro.git SuperPro   # 비공개면 아래 '토큰' 참고
cd SuperPro && pip3 install -r requirements.txt
```
> 비공개 repo 클론: GitHub → Settings → Developer settings → Personal access token(classic, repo 권한) 발급 →
> `git clone https://<토큰>@github.com/reoh97/superpro.git SuperPro`

### 2) 키 설정 (모의는 ANTHROPIC만, 실거래는 UPBIT도)
```bash
sudo nano /etc/superpro.env
```
```
ANTHROPIC_API_KEY=sk-ant-...      # AI 장세게이트(없으면 게이트만 OFF, 나머진 정상)
TELEGRAM_BOT_TOKEN=...            # 알림(선택)
TELEGRAM_CHAT_ID=...
# 실거래 전환 시에만:
# UPBIT_ACCESS_KEY=...   UPBIT_SECRET_KEY=...   (출금권한 OFF)
```

### 3) 자동실행 등록 (systemd — 정전·크래시 자동복귀)
```bash
# superpro.service 의 User/WorkingDirectory를 본인 계정(예: ubuntu)·경로로 수정
sed -i 's#/home/pi#/home/ubuntu#g; s#User=pi#User=ubuntu#' deploy/superpro.service
sudo cp deploy/superpro.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now superpro
systemctl status superpro           # 동작 확인
journalctl -u superpro -f           # 실시간 로그
```
> 기본 `config.yaml` 은 `mode: paper`(모의). 충분히 관찰 후 실거래로 바꾼다.

### 4) 폰/PC에서 대시보드 보기 (안전하게)
- **Tailscale**(추천): VPS·폰 둘 다 설치 → 폰에서 `http://<테일스케일IP>:8000`. 포트개방 불필요.
- 또는 SSH 터널: `ssh -L 8000:localhost:8000 ubuntu@<서버IP>` 후 PC에서 `http://localhost:8000`
- ⚠️ 방화벽으로 8000 포트 외부 직접개방 금지(대시보드 공개 위험).

## 운영 안전망 (이미 코드에 내장)
`run_all.py` 가 감시 스레드로 다음을 자동 처리한다 (`config.yaml` 의 `safety`/`telegram`):
- **차단기**: 합산자산 고점대비 `-max_drawdown_pct` 또는 당일 `-max_daily_loss_pct` → **신규진입 전면중단**
  (보유분은 각 전략대로 청산 계속. 일일차단은 다음 거래일 자동 해제). 상태는 `data/riskguard_*.json` 영속.
- **텔레그램 알림**: 가동/체결/오류/차단/생존신호(heartbeat). 미설정이면 자동 off.
- **자동 재시작**: 아래 systemd (정전·크래시 복귀).

## 1. 라즈베리파이 / 리눅스 VPS (systemd)
```bash
# 의존성
pip3 install -r requirements.txt
# 키는 환경파일에 (config.yaml 평문 금지)
sudo nano /etc/superpro.env        # UPBIT_*, ANTHROPIC_API_KEY, TELEGRAM_* 입력
# 서비스 등록 (deploy/superpro.service 안의 경로/User 수정 후)
sudo cp deploy/superpro.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now superpro
systemctl status superpro          # 동작 확인
journalctl -u superpro -f          # 실시간 로그
```

## 2. 텔레그램 알림 설정
1. 텔레그램 `@BotFather` → `/newbot` → **봇 토큰** 발급
2. 만든 봇과 대화 시작(아무 메시지) → `@userinfobot` 등으로 **본인 chat_id** 확인
3. `/etc/superpro.env` 에 `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` 넣고 `config.yaml` 의 `telegram.enabled: true`

## 3. 폰에서 보기 (원격 모니터)
봇은 Pi/VPS에서 돌리고, 대시보드(`http://<기기>:8000`)만 폰에서 본다.
- 집 Pi면 [Tailscale](https://tailscale.com) 설치 → 폰에서 같은 테일넷으로 안전 접속(포트개방 불필요)
- VPS면 방화벽으로 8000 포트 외부 차단 + Tailscale/SSH 터널로만 접속(대시보드 공개 금지)

## 4. Windows (작업 스케줄러)
- 트리거: "시스템 시작 시" + "작업 실패 시 다시 시작"
- 동작: `python C:\경로\SuperPro\run_all.py`
- 절전/최대절전 끄기(컴퓨터가 자면 봇도 멈춤)

## 체크리스트 (실거래 전)
- [ ] `mode: live`, 업비트 키(**출금권한 OFF**, 가능하면 IP 화이트리스트)
- [ ] `safety.max_drawdown_pct`/`max_daily_loss_pct` 본인 위험성향에 맞게
- [ ] 텔레그램 알림 수신 테스트 완료
- [ ] systemd 자동재시작 동작 확인 (`sudo systemctl restart superpro`)
- [ ] `reconcile.py` 로 장부↔실계정 정합성 점검 (cron 권장)
- [ ] 소액으로 1~2개월 모의↔실거래 괴리 관찰 후 증액
