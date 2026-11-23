"""설정 로드 (stock/.env)."""
import os
import re
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent          # stock/
PROJECT_ROOT = ROOT.parent                      # naver/

load_dotenv(ROOT / ".env")


def _get(name: str, default: str = "") -> str:
    v = os.getenv(name)
    return default if v is None or v.strip() == "" else v.strip()


def _flag(name: str, default: str) -> bool:
    return _get(name, default).lower() in ("1", "true", "yes", "y", "on")


# ── 네이버 계정 ──
NAVER_ID = _get("NAVER_ID")
NAVER_PW = _get("NAVER_PW")
AUTO_LOGIN = _flag("AUTO_LOGIN", "1")

# ── 프리미엄콘텐츠 채널 ──
CHANNEL_URL = _get("CHANNEL_URL", "https://contents.premium.naver.com/insightstrader/insight").rstrip("/")
CHANNEL_PATH = CHANNEL_URL.split("premium.naver.com", 1)[-1]          # /insightstrader/insight
LIST_URL = f"{CHANNEL_URL}/contents"

# ── 텔레그램 ──
TELEGRAM_BOT_TOKEN = _get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = _get("TELEGRAM_CHAT_ID")      # 비우면 getUpdates 로 자동 탐지 후 state 에 저장


def _parse_targets(raw: str) -> list[dict]:
    """TELEGRAM_TARGETS="이름|봇토큰|chat_id, 이름|봇토큰|chat_id" → [{name, token, chat_id}]"""
    out = []
    for part in re.split(r"[,\n]+", raw):
        part = part.strip()
        if not part:
            continue
        fields = [f.strip() for f in part.split("|")]
        if len(fields) == 3:
            name, token, chat_id = fields
        elif len(fields) == 2:
            token, chat_id = fields
            name = chat_id
        else:
            continue
        out.append({"name": name, "token": token, "chat_id": chat_id})
    return out


# 여러 봇/채팅으로 동시에 보낼 때. 비어 있으면 TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID 단일 대상 사용
TELEGRAM_TARGETS = _parse_targets(_get("TELEGRAM_TARGETS"))

# ── 동작 ──
POLL_INTERVAL_SEC = float(_get("POLL_INTERVAL_SEC", "3600"))  # 새 글 확인 주기(기본 1시간)
SESSION_CHECK_SEC = float(_get("SESSION_CHECK_SEC", "1800"))  # 로그인 세션 확인 주기
TODAY_ONLY = _flag("TODAY_ONLY", "1")                         # 오늘 올라온 글만 전송
MAX_BACKFILL = int(_get("MAX_BACKFILL", "20"))                # 한 번에 밀려 보낼 최대 개수
SUMMARY_MAX_CHARS = int(_get("SUMMARY_MAX_CHARS", "2500"))    # 요약 본문 최대 길이
DAILY_DIGEST_HOUR = _get("DAILY_DIGEST_HOUR", "")             # 예: 18 → 매일 18시에 하루 요약 모아보내기 (비우면 안 함)

# ── LLM 요약(선택) ──
OPENAI_API_KEY = _get("OPENAI_API_KEY")
OPENAI_MODEL = _get("OPENAI_MODEL", "gpt-4o-mini")
ANTHROPIC_API_KEY = _get("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL = _get("ANTHROPIC_MODEL", "claude-3-5-haiku-latest")

# ── 브라우저 (댓글 봇과 별도 프로필/포트) ──
DEBUG_PORT = int(_get("DEBUG_PORT", "9223"))
CHROME_PATH = _get("CHROME_PATH", r"C:\Program Files\Google\Chrome\Application\chrome.exe")
CHROME_USER_DATA = str((ROOT / _get("CHROME_USER_DATA", ".chrome-stock")).resolve())

# ── 경로 ──
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
STATE_FILE = ROOT / "state.json"

LOGIN_URL = "https://nid.naver.com/nidlogin.login"
NAVER_MAIN = "https://www.naver.com"
