# 실전 배포 가이드 (24시간 운영)

봇은 켜둔 컴퓨터에서만 돈다. 끄면 매매 정지(상태는 저장돼 재시작 시 이어감).
24시간 돌리려면 **항상 켜둘 한국 IP 기기**가 필요하다. ⚠️ 업비트는 한국 IP에서만 응답.

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
