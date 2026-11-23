# 프리미엄콘텐츠 → 텔레그램 모니터 사용법 (`stock/`)

네이버 프리미엄콘텐츠 채널(기본: [insightstrader/insight](https://contents.premium.naver.com/insightstrader/insight))에
**그날 새로 올라온 글**을 1시간마다 확인해 요약한 뒤 텔레그램으로 보내는 상주 스크립트입니다.

---

## 1. 한 줄 요약

```powershell
cd C:\project\naver\stock
python main.py
```

- 처음 실행 시 전용 Chrome 창(`stock/.chrome-stock`, 포트 9223)이 열리고 네이버에 자동 로그인합니다.
- 이후 **1시간마다** 채널 글 목록을 확인 → 오늘 날짜의 새 글만 본문을 읽어 요약 → 등록된 모든 텔레그램 수신자에게 전송합니다.
- 창을 닫지 말고 그대로 두면 됩니다. 종료는 터미널에서 `Ctrl+C`.

---

## 2. 폴더 구성

| 경로 | 역할 |
|---|---|
| `stock/main.py` | 진입점. 세션 관리 · 폴링 루프 · 전송 이력 저장 |
| `stock/config.py` | `.env` 를 읽어 설정값으로 노출 |
| `stock/browser.py` | 전용 Chrome 실행/접속, 네이버 로그인, 쿠키 추출 |
| `stock/premium.py` | 채널 목록/본문 파싱 (requests + BeautifulSoup) |
| `stock/summarize.py` | 규칙 기반(또는 LLM) 요약 및 텔레그램 메시지 조립 |
| `stock/telegram.py` | 텔레그램 Bot API 전송 (여러 수신자 동시 전송) |
| `stock/.env` | **실제 설정값 (계정·토큰). git 에 올리지 않음** |
| `stock/.env.example` | 설정 템플릿 |
| `stock/state.json` | 이미 보낸 글 ID 이력 (자동 생성) |
| `stock/logs/stock.log` | 실행 로그 (자동 생성) |
| `stock/.chrome-stock/` | 로그인 세션이 저장되는 Chrome 프로필 (자동 생성) |

---

## 3. 설치 (최초 1회)

```powershell
cd C:\project\naver\stock
pip install -r requirements.txt
```

Chrome 이 설치되어 있어야 합니다. 경로가 기본 위치가 아니면 `.env` 에 `CHROME_PATH=` 를 지정하세요.

---

## 4. 설정 (`stock/.env`)

`.env.example` 을 복사해 `.env` 로 만들고 값을 채웁니다.

### 4-1. 네이버 계정

```
NAVER_ID=아이디
NAVER_PW=비밀번호
AUTO_LOGIN=1        # 1: 자동 입력, 0: 열린 Chrome 창에서 직접 로그인
```

- 채널을 **구독 중인 계정**이어야 유료 본문까지 읽습니다.
- 캡차가 뜨면 자동 입력이 멈추므로, 열린 Chrome 창에서 직접 로그인하면 스크립트가 감지하고 이어서 진행합니다(최대 15분 대기).

### 4-2. 텔레그램 수신자 (여러 명)

```
TELEGRAM_TARGETS=기정|봇토큰1|chat_id1, 수빈|봇토큰2|chat_id2
```

- 형식: `이름|봇토큰|chat_id` 를 쉼표로 나열. 이름은 로그 표시용.
- **각 수신자는 자기 봇에 텔레그램에서 `/start` 를 한 번 보내 둬야** 메시지를 받을 수 있습니다.
- 한 명에게 실패해도 나머지에게는 계속 보냅니다. 전송 실패는 로그에만 남고 프로그램은 멈추지 않습니다.
- `TELEGRAM_TARGETS` 를 비우면 아래 단일 설정을 씁니다. `TELEGRAM_CHAT_ID` 가 비어 있으면 봇에 메시지를 보낸 사람을 자동 탐지합니다.

```
TELEGRAM_BOT_TOKEN=봇토큰
TELEGRAM_CHAT_ID=
```

chat_id 를 모르면: 봇에 아무 메시지나 보낸 뒤 브라우저에서
`https://api.telegram.org/bot<봇토큰>/getUpdates` 를 열어 `"chat":{"id": ...}` 값을 확인합니다.

### 4-3. 동작 설정

```
POLL_INTERVAL_SEC=3600   # 새 글 확인 주기(초). 3600 = 1시간
SESSION_CHECK_SEC=1800   # 로그인 상태 재확인 주기(초)
TODAY_ONLY=1             # 1: 오늘 날짜 글만 전송(지난 글은 건너뜀)
MAX_BACKFILL=20          # 한 번에 처리할 최대 새 글 수
SUMMARY_MAX_CHARS=2500   # 이 길이 이하면 전문, 넘으면 핵심 발췌
DAILY_DIGEST_HOUR=       # 예: 18 → 매일 18시에 그날 글 목록 한 번 더 전송(비우면 끔)
```

- 주기를 바꾸고 싶으면 `POLL_INTERVAL_SEC` 만 수정 후 재시작 (예: 10분 = `600`).
- 처음 실행하면 `state.json` 이 비어 있으므로 **오늘 올라온 글은 전부** 한 번에 전송됩니다. 어제 이전 글은 `TODAY_ONLY=1` 이라 보내지 않고 "건너뜀"으로 기록합니다.

### 4-4. LLM 요약 (선택)

```
OPENAI_API_KEY=            # 있으면 OpenAI 로 요약
OPENAI_MODEL=gpt-4o-mini
ANTHROPIC_API_KEY=         # 있으면 Anthropic 으로 요약
ANTHROPIC_MODEL=claude-3-5-haiku-latest
```

키가 없으면 규칙 기반 요약을 씁니다(✅ 📌 ❝ 같은 강조 문단과 매수/매도/비중/숫자 포함 문장을 우선 발췌).
LLM 을 쓰려면 `pip install openai` 또는 `pip install anthropic` 추가.

### 4-5. 브라우저

```
DEBUG_PORT=9223
CHROME_USER_DATA=.chrome-stock
```

댓글 봇(`src/`, 포트 9222, `.chrome-debug`)과 충돌하지 않도록 **다른 포트·다른 프로필**을 씁니다.
Chrome 136 이상은 평소 쓰는 기본 프로필에 원격 디버깅을 허용하지 않으므로 전용 프로필이 필수입니다
(그래서 기존 크롬 로그인 상태를 가져올 수 없고, 전용 창에서 한 번 로그인해야 합니다).

---

## 5. 실행 · 중지 · 재시작

### 실행

```powershell
cd C:\project\naver\stock
python main.py
```

정상 시작 로그 예:

```
=== 프리미엄콘텐츠 → 텔레그램 모니터 시작 ===
채널: https://contents.premium.naver.com/insightstrader/insight / 폴링 3600초 / 요약: 규칙 기반
...
새 글 없음. (목록 20개, 최신: 10월 13일 모닝브리핑)
전송 성공 기정:O/수빈:O: [모닝브리핑] 10월 13일 모닝브리핑 (4729자)
```

### 중지

실행 중인 터미널에서 `Ctrl+C`. 터미널을 잃어버렸으면:

```powershell
Get-CimInstance Win32_Process -Filter "name='python.exe'" |
  Where-Object { $_.CommandLine -match 'main\.py' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

> 주의: 터미널 창만 닫으면 python 이 백그라운드에 남아 **중복 전송**될 수 있습니다. 반드시 위처럼 프로세스를 확인하세요.

### 재시작

`.env` 를 바꾼 뒤에는 중지 → 다시 실행. 로그인 세션은 `.chrome-stock` 에 남아 있어 다시 로그인하지 않아도 됩니다.

### 백그라운드 실행 (터미널 안 띄우기)

```powershell
cd C:\project\naver\stock
Start-Process -WindowStyle Hidden python -ArgumentList "main.py"
```

로그는 `stock\logs\stock.log` 로 확인:

```powershell
Get-Content C:\project\naver\stock\logs\stock.log -Tail 20 -Wait -Encoding UTF8
```

---

## 6. 전송 메시지 형식

```
<제목>
카테고리 · 작성자 · 2026.10.13. 오전 7:42

(요약 또는 전문)

…핵심만 발췌 (원문 4729자)      ← 길어서 발췌했을 때만
🔗 관련 링크 (최대 5개)
원문 보기
```

- 4,000자가 넘으면 자동으로 나눠 보냅니다.
- 요약 끝의 `[투자 유의사항 안내]` 이후 면책 문구는 제거합니다.

---

## 7. 자주 겪는 문제

| 증상 | 원인 / 해결 |
|---|---|
| 텔레그램에 아무것도 안 옴 | 수신자가 봇에 `/start` 를 안 보냄 → 보내고 다시 실행. 로그에 `403` 이면 이 경우 |
| `로그인 필요` 반복 | 세션 만료. 열린 Chrome 창에서 직접 로그인하면 자동 재개. 안 되면 `.chrome-stock` 폴더 삭제 후 재실행 |
| 본문이 `[유료 콘텐츠]` 로만 옴 | 해당 계정이 채널 구독 중이 아님 |
| 같은 글이 두 번 옴 | python 프로세스가 2개 떠 있음 → 5번 "중지" 명령으로 모두 종료 후 하나만 실행 |
| 과거 글을 다시 보내고 싶음 | `state.json` 에서 해당 content_id 항목을 지우고 `TODAY_ONLY=0` 으로 잠시 실행 |
| Chrome 이 안 뜸 | `.env` 에 `CHROME_PATH=C:\Program Files\Google\Chrome\Application\chrome.exe` 지정 |
| PC 가 절전에 들어감 | 스크립트가 실행 중엔 절전을 막지만(`SetThreadExecutionState`), 덮개 닫힘/강제 절전은 막지 못함 |

---

## 8. 현재 운영 설정 (2026-10-13 기준)

- 계정: `wzxcv123`
- 채널: insightstrader/insight
- 수신자: 기정(`@fish_stock_trend_bot`), 수빈(`@INTNAVERSTOCK_bot`)
- 확인 주기: **1시간** (`POLL_INTERVAL_SEC=3600`)
- 오늘 글만 전송 (`TODAY_ONLY=1`), 규칙 기반 요약
