"""설정 로드 및 상수 정의."""
import os
from pathlib import Path

from dotenv import load_dotenv

# 프로젝트 루트
ROOT = Path(__file__).resolve().parent.parent

load_dotenv(ROOT / ".env")


def _get(name: str, default: str = "") -> str:
    v = os.getenv(name)
    return default if v is None or v.strip() == "" else v.strip()


def _flag(name: str, default: str) -> bool:
    return _get(name, default).lower() in ("1", "true", "yes", "y", "on")


NAVER_ID = _get("NAVER_ID")
NAVER_PW = _get("NAVER_PW")
# 1: 아이디/비번을 스크립트가 붙여넣어 로그인 시도, 0: 로그인 페이지만 열고 사람이 직접 로그인
AUTO_LOGIN = _flag("AUTO_LOGIN", "0")
BLOG_ID = _get("BLOG_ID", "studiochoix")

# 댓글을 달 대상 글 logNo. 비워두면 "새 글 감지" 모드로 동작.
POST_LOGNO = _get("POST_LOGNO")

# COMMENT_TEXT 안의 리터럴 "\n" 을 실제 개행으로 변환
COMMENT_TEXT = _get("COMMENT_TEXT", "").replace("\\n", "\n")

# 비밀댓글 체크 여부 (블로그 공지가 비밀댓글을 요구하므로 기본 ON)
SECRET_COMMENT = _flag("SECRET_COMMENT", "1")

TARGET_HOUR = int(_get("TARGET_HOUR", "9"))
TARGET_MINUTE = int(_get("TARGET_MINUTE", "0"))

# 폴링 간격(초)
POLL_INTERVAL_IDLE = float(_get("POLL_INTERVAL_IDLE", "30"))      # 목표 10분 전까지
POLL_INTERVAL_NORMAL = float(_get("POLL_INTERVAL_NORMAL", "5"))   # 목표 10분 전 ~ 1분 전
POLL_INTERVAL_FAST = float(_get("POLL_INTERVAL_FAST", "0.15"))    # 목표 2분 전부터 cbox API 상태 조회 간격
SESSION_REFRESH_SEC = float(_get("SESSION_REFRESH_SEC", "600"))   # 대기 중 세션 생존 확인 주기

# ── 브라우저 ──
# Attach 모드: 원격 디버깅 포트로 띄운 Chrome 에 연결해 "새 탭"에서 작업.
# Chrome 136+ 는 기본 사용자 데이터 폴더(%LOCALAPPDATA%\Google\Chrome\User Data)에서는
# --remote-debugging-port 를 무시하므로, 반드시 별도 폴더를 사용해야 한다.
ATTACH_MODE = _flag("ATTACH_MODE", "1")
DEBUG_PORT = int(_get("DEBUG_PORT", "9222"))
CHROME_PATH = _get(
    "CHROME_PATH", r"C:\Program Files\Google\Chrome\Application\chrome.exe"
)
CHROME_DEBUG_USER_DATA = str((ROOT / _get("CHROME_DEBUG_USER_DATA", ".chrome-debug")).resolve())
CHROME_PROFILE_NAME = _get("CHROME_PROFILE_NAME", "Default")

# Selenium 직접 구동 모드(ATTACH_MODE=0)에서 쓰는 고정 프로필
CHROME_USER_DATA_DIR = str((ROOT / _get("CHROME_USER_DATA_DIR", ".chrome-profile")).resolve())

# 경로
LOG_DIR = ROOT / "logs"
SCREENSHOT_DIR = ROOT / "screenshots"
LOG_DIR.mkdir(exist_ok=True)
SCREENSHOT_DIR.mkdir(exist_ok=True)

# URL
LOGIN_URL = "https://nid.naver.com/nidlogin.login"
NAVER_MAIN = "https://www.naver.com"
RSS_URL = f"https://rss.blog.naver.com/{BLOG_ID}.xml"
BLOG_URL = f"https://blog.naver.com/{BLOG_ID}"


def post_url(log_no) -> str:
    return f"https://blog.naver.com/{BLOG_ID}/{log_no}"


def post_view_url(log_no) -> str:
    """iframe 없이 본문+댓글이 바로 렌더링되는 PostView URL."""
    return f"https://blog.naver.com/PostView.naver?blogId={BLOG_ID}&logNo={log_no}"


def mobile_post_url(log_no) -> str:
    return f"https://m.blog.naver.com/{BLOG_ID}/{log_no}"


def mobile_comment_url(log_no) -> str:
    """모바일 댓글 모달(입력창이 바로 노출됨)."""
    return f"https://m.blog.naver.com/CommentList.naver?blogId={BLOG_ID}&logNo={log_no}"
