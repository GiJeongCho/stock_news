"""네이버 프리미엄콘텐츠 채널 새 글 → 요약 → 텔레그램 전송 (상주 스크립트).

실행: cd stock; python main.py
- 로그인 세션은 .chrome-stock 프로필에 저장되어 재실행 시 유지
- 새 글은 POLL_INTERVAL_SEC 마다 확인해 즉시 전송, 전송 이력은 state.json 에 저장
- 텔레그램 chat_id 가 비어 있으면 봇에 메시지를 보낸 사람을 자동 탐지
"""
import ctypes
import json
import logging
import re
import sys
import time
from datetime import datetime, date

import config
import browser
import premium
import summarize
import telegram

log = logging.getLogger("main")


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout),
                  logging.FileHandler(config.LOG_DIR / "stock.log", encoding="utf-8")],
    )


def keep_awake(enable=True):
    try:
        flags = 0x80000000 | (0x00000001 if enable else 0)
        ctypes.windll.kernel32.SetThreadExecutionState(flags)
    except Exception:
        pass


# ── 상태 저장 ──
def load_state() -> dict:
    if config.STATE_FILE.exists():
        try:
            return json.loads(config.STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"sent": {}, "chat_id": config.TELEGRAM_CHAT_ID or None, "digest_sent_on": None}


def save_state(state: dict):
    tmp = config.STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(config.STATE_FILE)


def ensure_targets(state: dict) -> bool:
    """전송 대상이 하나라도 있으면 True. 단일 봇 모드에서 chat_id 가 없으면 자동 탐지."""
    if telegram.targets(state):
        return True
    cid = telegram.discover_chat_id()
    if cid:
        state["chat_id"] = cid
        save_state(state)
        log.info("텔레그램 chat_id 설정: %s", cid)
        telegram.send_message(cid, "✅ 연결되었습니다. 인사이트 마스터플랜 새 글을 이 채팅으로 보내드립니다.")
        return True
    log.warning("텔레그램 전송 대상 없음. 봇에게 /start 를 보내 주세요.")
    return False


class Runner:
    def __init__(self):
        self.driver = None
        self.session = None
        self.state = load_state()
        self.last_session_check = 0.0
        self.posts_today: dict[str, premium.Post] = {}

    # ── 세션 ──
    def connect(self):
        self.driver = browser.connect()
        if not browser.login(self.driver, max_wait_manual=900):
            raise RuntimeError("로그인 실패")
        self.refresh_session()
        self.last_session_check = time.time()

    def refresh_session(self):
        # 채널 페이지에 머물러 두면 .naver.com 쿠키를 그대로 내보낼 수 있다
        browser.safe_get(self.driver, config.CHANNEL_URL)
        time.sleep(0.8)
        self.session = premium.build_session(browser.export_cookies(self.driver))

    def check_session(self):
        if time.time() - self.last_session_check < config.SESSION_CHECK_SEC:
            return
        self.last_session_check = time.time()
        if not browser.is_logged_in(self.driver):
            log.warning("세션 만료 → 재로그인")
            if not browser.login(self.driver, max_wait_manual=900):
                raise RuntimeError("재로그인 실패")
        self.refresh_session()

    # ── 수집/전송 ──
    def poll(self):
        has_targets = ensure_targets(self.state)
        try:
            items, _ = premium.fetch_list(self.session)
        except premium.AuthError:
            log.warning("인증 오류 → 세션 갱신 후 재시도")
            self.last_session_check = 0
            self.check_session()
            items, _ = premium.fetch_list(self.session)

        sent = self.state["sent"]
        new_items = [it for it in items if it.content_id not in sent]

        # 오늘 글만 전송. 다른 날짜 글은 '본 것'으로 기록만 한다.
        if config.TODAY_ONLY and new_items:
            today = date.today()
            keep, skip = [], []
            for it in new_items:
                d = premium.content_id_time(it.content_id)
                d = d.date() if d else None
                if d is None:
                    m = re.match(r"(\d{4})\.(\d{2})\.(\d{2})", it.date or "")
                    d = date(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else today
                (keep if d == today else skip).append(it)
            for it in skip:
                sent[it.content_id] = {"title": it.title, "skipped": True, "date": it.date,
                                       "at": datetime.now().isoformat(timespec="seconds")}
            if skip:
                save_state(self.state)
                log.info("오늘(%s) 글이 아닌 %d개는 건너뜀.", today, len(skip))
            new_items = keep
        if len(new_items) > config.MAX_BACKFILL:
            # 비정상적으로 많이 밀린 경우 최신 N개만
            for it in new_items[config.MAX_BACKFILL:]:
                sent[it.content_id] = {"title": it.title, "skipped": True, "at": datetime.now().isoformat(timespec="seconds")}
            new_items = new_items[: config.MAX_BACKFILL]
            save_state(self.state)

        if not new_items:
            log.info("새 글 없음. (목록 %d개, 최신: %s)", len(items), items[0].title if items else "-")
            return

        if not has_targets:
            log.warning("새 글 %d개 발견했지만 전송 대상이 없어 보류합니다.", len(new_items))
            return

        for it in reversed(new_items):   # 오래된 것부터
            try:
                post = premium.fetch_post(self.session, it)
            except premium.AuthError:
                self.last_session_check = 0
                self.check_session()
                post = premium.fetch_post(self.session, it)
            msg = summarize.build_message(post)
            results = telegram.broadcast(msg, self.state)
            ok = any(results.values())
            log.info("전송 %s %s: [%s] %s (%d자)",
                     "성공" if ok else "실패",
                     "/".join(f"{k}:{'O' if v else 'X'}" for k, v in results.items()),
                     post.category, post.title, len(post.body_text))
            if ok:
                sent[it.content_id] = {"title": post.title, "published": post.published,
                                       "at": datetime.now().isoformat(timespec="seconds")}
                save_state(self.state)
                pub = premium.parse_published(post.published) or premium.content_id_time(it.content_id)
                if pub and pub.date() == date.today():
                    self.posts_today[it.content_id] = post
            time.sleep(1.0)

    def maybe_digest(self):
        if not config.DAILY_DIGEST_HOUR:
            return
        now = datetime.now()
        today = now.date().isoformat()
        if now.hour < int(config.DAILY_DIGEST_HOUR) or self.state.get("digest_sent_on") == today:
            return
        if not telegram.targets(self.state):
            return
        posts = sorted(self.posts_today.values(), key=lambda p: p.content_id)
        if posts:
            telegram.broadcast(summarize.build_digest(posts, now.strftime("%m월 %d일")), self.state)
        self.state["digest_sent_on"] = today
        save_state(self.state)
        self.posts_today = {}

    def run(self):
        while True:
            try:
                if self.driver is None or self.session is None:
                    self.connect()
                self.check_session()
                self.poll()
                self.maybe_digest()
                # 전송 대상이 아직 없으면(봇에 /start 대기) 짧게 재확인
                time.sleep(config.POLL_INTERVAL_SEC if telegram.targets(self.state) else 30)
            except KeyboardInterrupt:
                raise
            except Exception as e:
                log.exception("오류, 30초 후 재시도: %s", e)
                self.driver = None
                self.session = None
                time.sleep(30)


def main():
    setup_logging()
    log.info("=== 프리미엄콘텐츠 → 텔레그램 모니터 시작 ===")
    log.info("채널: %s / 폴링 %.0f초 / 요약: %s", config.CHANNEL_URL, config.POLL_INTERVAL_SEC,
             "LLM" if (config.OPENAI_API_KEY or config.ANTHROPIC_API_KEY) else "규칙 기반")
    if not config.TELEGRAM_BOT_TOKEN:
        log.error("TELEGRAM_BOT_TOKEN 이 없습니다.")
        return
    keep_awake(True)
    try:
        Runner().run()
    except KeyboardInterrupt:
        log.info("사용자 중단.")
    finally:
        keep_awake(False)


if __name__ == "__main__":
    main()
