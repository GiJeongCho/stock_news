"""오케스트레이션: Chrome 연결(새 탭) → 로그인 확인 → 댓글 오픈 대기 → 댓글 작성.

한 번 실행해 두면 목표 시각까지 상주하며,
- 세션 생존 확인(주기적) 및 끊김 시 재로그인 시도
- Chrome/탭이 닫히거나 네트워크 오류가 나도 재연결 후 계속
- PC 절전 방지
를 스스로 처리한다. 댓글은 한 번만 등록한다.
"""
import ctypes
import logging
import sys
import time
from datetime import datetime, timedelta

import config
import cbox
import comment as comment_mod
import monitor
from browser import build_driver, safe_get
from login import login, is_logged_in


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(config.LOG_DIR / "run.log", encoding="utf-8"),
        ],
    )


log = logging.getLogger("main")


def keep_awake(enable: bool = True):
    """Windows 절전/디스플레이 꺼짐 방지 (스크립트 실행 중)."""
    ES_CONTINUOUS = 0x80000000
    ES_SYSTEM_REQUIRED = 0x00000001
    ES_DISPLAY_REQUIRED = 0x00000002
    try:
        flags = ES_CONTINUOUS | ((ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED) if enable else 0)
        ctypes.windll.kernel32.SetThreadExecutionState(flags)
        if enable:
            log.info("절전 방지 활성화.")
    except Exception as e:
        log.warning("절전 방지 설정 실패: %s", e)


def target_dt() -> datetime:
    now = datetime.now()
    t = now.replace(hour=config.TARGET_HOUR, minute=config.TARGET_MINUTE, second=0, microsecond=0)
    if t < now - timedelta(hours=1):
        # 이미 1시간 이상 지난 시각이면 '지금' 집중 모드
        return now
    return t


def seconds_to_target() -> float:
    return (target_dt() - datetime.now()).total_seconds()


def resolve_target_logno(driver):
    """댓글을 달 글의 logNo 결정. POST_LOGNO 가 있으면 그대로, 없으면 새 글 감지."""
    if config.POST_LOGNO:
        log.info("대상 글 고정: logNo=%s (%s)", config.POST_LOGNO, config.mobile_post_url(config.POST_LOGNO))
        return config.POST_LOGNO

    base = monitor.latest_logno(driver)
    log.info("POST_LOGNO 미설정 → 새 글 감지 모드. 시작 시점 최신 logNo = %s", base)
    while True:
        cur = monitor.latest_logno(driver)
        if cur and (base is None or cur > base):
            log.info("새 글 감지! logNo=%s", cur)
            return str(cur)
        log.info("새 글 대기 중... 현재 최신=%s, 목표까지 %.0f초", cur, seconds_to_target())
        time.sleep(config.POLL_INTERVAL_NORMAL if seconds_to_target() > 60 else 1.0)


def ensure_session(driver, remaining: float) -> bool:
    """로그인 세션 확인. 끊겼으면 자동 입력 → 수동 대기 순으로 복구 시도."""
    if is_logged_in(driver):
        return True
    log.warning("세션이 끊겼습니다. 재로그인을 시도합니다.")
    # 사람이 자리에 없을 수 있으므로 1차는 자동 입력으로 시도
    orig = config.AUTO_LOGIN
    config.AUTO_LOGIN = True
    try:
        ok = login(driver, max_wait_manual=int(max(120, min(remaining - 30, 1800))))
    finally:
        config.AUTO_LOGIN = orig
    return ok


def wait_until_comment_open(driver, log_no):
    """댓글 기능이 켜질 때까지 대기.

    - 목표 10분 전까지: HTTP 폴링(모바일+PC, 30s) + 10분마다 세션 생존 확인
    - 목표 10분 전 ~ 1분 전: HTTP 폴링(5s)
    - 목표 1분 전 ~ 10분 후: 브라우저에서 PC 글을 반복 로드하며 댓글 버튼 확인(가장 빠른 반응)
    - 목표 10분 후부터: HTTP 폴링(5s)로 복귀(과부하 방지), 열리면 즉시 진행
    HTTP 로 '열림'이 확인되면 즉시 브라우저로 넘어간다.
    """
    last_refresh = time.time()
    while True:
        remaining = seconds_to_target()

        if -600 <= remaining <= 60:
            comment_mod.load_post(driver, log_no)
            if comment_mod.comment_button_present(driver, log_no):
                log.info("브라우저에서 댓글 버튼 확인! (목표 %+.0f초)", -remaining)
                return
            time.sleep(config.POLL_INTERVAL_FAST)
            continue

        state = monitor.comment_open_http(log_no)
        if state is True:
            log.info("HTTP 로 댓글 활성 확인! (목표까지 %.0f초) → 브라우저 로드", remaining)
            for _ in range(5):
                comment_mod.load_post(driver, log_no, wait_sec=0.3)
                if comment_mod.comment_button_present(driver, log_no):
                    return
                log.info("브라우저(PC)에서는 아직 버튼 미노출. 재확인.")
                time.sleep(0.7)
            # PC 에서 계속 안 보이면 모바일 경로로 바로 작성하도록 반환
            log.warning("PC 페이지에 버튼이 보이지 않아 작성 단계로 넘어갑니다(모바일 폴백 포함).")
            return

        interval = config.POLL_INTERVAL_NORMAL if abs(remaining) <= 600 else config.POLL_INTERVAL_IDLE
        log.info("댓글 비활성(HTTP=%s). 목표까지 %.0f초, %.0f초 후 재확인", state, remaining, interval)

        if time.time() - last_refresh >= config.SESSION_REFRESH_SEC and remaining > 120:
            last_refresh = time.time()
            ensure_session(driver, remaining)
            comment_mod.load_post(driver, log_no)

        time.sleep(interval)


def _posted_marker(log_no):
    return config.LOG_DIR / f"posted_{log_no}.json"


def _refresh_client_cookies(driver, client, log_no):
    """브라우저 탭을 글 페이지에 두고 최신 쿠키를 API 클라이언트에 반영."""
    try:
        safe_get(driver, config.post_view_url(log_no))
        time.sleep(0.8)
        ck = cbox.cookies_from_driver(driver)
        if "NID_AUT" in ck and "NID_SES" in ck:
            client.set_cookies(ck)
            return True
        log.warning("브라우저 쿠키에 로그인 정보 없음.")
    except Exception as e:
        log.warning("쿠키 갱신 실패: %s", e)
    return False


def fast_wait_and_post(driver, client: cbox.CboxClient, log_no, text: str) -> bool | None:
    """cbox API 로 댓글 오픈을 감지(약 50ms 응답)하고 열리는 즉시 API 로 등록.

    - 목표 10분 전까지: 30초 간격 + SESSION_REFRESH_SEC 마다 세션/쿠키 갱신
    - 10분 전 ~ 2분 전: 2초 간격
    - 2분 전 ~ 5분 후: POLL_INTERVAL_FAST(기본 0.15초) 간격, 토큰 30초마다 미리 갱신
    - 이후: 1초 간격
    반환: True=등록 성공, False=API 등록 실패(UI 폴백 필요), None 은 없음.
    """
    last_refresh = time.time()
    none_streak = 0
    while True:
        remaining = seconds_to_target()
        if remaining > 600:
            interval = 30.0
        elif remaining > 120:
            interval = 2.0
        elif remaining > -300:
            interval = config.POLL_INTERVAL_FAST
        else:
            interval = 1.0

        # 세션/쿠키 갱신(여유 있을 때만)
        if remaining > 150 and time.time() - last_refresh >= config.SESSION_REFRESH_SEC:
            last_refresh = time.time()
            if ensure_session(driver, remaining):
                _refresh_client_cookies(driver, client, log_no)
        # 토큰은 열리기 전에 미리 확보(오픈 순간엔 create 1회만 날린다)
        if remaining <= 150:
            client.ensure_token(max_age=30.0)
        else:
            client.ensure_token(max_age=300.0)

        t0 = time.perf_counter()
        state = client.status()
        if state is True:
            t_detect = datetime.now()
            log.info("★ 댓글 오픈 감지 (목표 %+.3f초, 상태조회 %.0fms)", -remaining, (time.perf_counter() - t0) * 1000)
            for attempt in range(1, 4):
                t1 = time.perf_counter()
                ok, resp = client.create(text, secret=config.SECRET_COMMENT)
                dt = (time.perf_counter() - t1) * 1000
                code, msg = resp.get("code"), resp.get("message")
                if ok:
                    cl = ((resp.get("result") or {}).get("commentList") or [{}])[0]
                    log.info("★★ API 등록 성공 %.0fms — commentNo=%s user=%s secret=%s (감지→등록 %.3f초)",
                             dt, cl.get("commentNo"), cl.get("userName"), not cl.get("open", True),
                             (datetime.now() - t_detect).total_seconds())
                    _posted_marker(log_no).write_text(
                        f'{{"commentNo":"{cl.get("commentNo")}","at":"{datetime.now().isoformat()}"}}', encoding="utf-8")
                    return True
                log.warning("API 등록 실패 %d/3 (%.0fms): code=%s %s", attempt, dt, code, msg)
                if str(code) == cbox.CODE_COMMENT_CLOSED:
                    break  # 아직 안 열린 것으로 판정 → 감지 루프로 복귀
                client.fetch_token()
                time.sleep(0.15)
            else:
                return False  # 3회 실패 → UI 폴백
            continue

        if state is None:
            none_streak += 1
            if none_streak % 20 == 1:
                log.warning("상태 조회 실패 연속 %d회 (네트워크/인증?)", none_streak)
            if none_streak >= 40 and remaining > 120:
                # 인증 문제 가능성 → 세션 복구
                if ensure_session(driver, remaining):
                    _refresh_client_cookies(driver, client, log_no)
                none_streak = 0
        else:
            none_streak = 0
            if interval >= 1.0:
                log.info("댓글 비활성(API). 목표까지 %.0f초, %.1f초 후 재확인", remaining, interval)

        time.sleep(interval)


def run_once(driver, log_no) -> bool:
    """대기 → 작성. 등록 성공 시 True."""
    if _posted_marker(log_no).exists():
        log.error("이미 등록 완료 기록이 있습니다(%s). 중복 등록 방지를 위해 종료합니다.", _posted_marker(log_no))
        return True

    # 1) 고속 경로: cbox API 직접 감지·등록
    try:
        client = cbox.CboxClient(config.BLOG_ID, log_no)
        if _refresh_client_cookies(driver, client, log_no):
            tok = client.fetch_token()
            st = client.status()
            log.info("API 준비 완료: blogNo=%s objectId=%s token=%s 현재상태=%s",
                     client.blog_no, client.object_id, "OK" if tok else "없음", st)
            res = fast_wait_and_post(driver, client, log_no, config.COMMENT_TEXT)
            if res:
                return True
            log.warning("API 등록 실패 → 브라우저 UI 경로로 폴백합니다.")
        else:
            log.warning("API 클라이언트에 로그인 쿠키를 넣지 못함 → 브라우저 UI 경로 사용.")
    except Exception as e:
        log.exception("고속 경로 오류 → 브라우저 UI 경로로 폴백: %s", e)

    # 2) 폴백: 기존 브라우저 UI 경로
    wait_until_comment_open(driver, log_no)

    ok = comment_mod.write_comment(driver, log_no, config.COMMENT_TEXT)
    if not ok:
        # 세션 문제였을 수 있으니 한 번 복구 후 재시도
        log.warning("1차 등록 실패. 세션 확인 후 재시도합니다.")
        if ensure_session(driver, 600):
            ok = comment_mod.write_comment(driver, log_no, config.COMMENT_TEXT)
    return ok


def main():
    setup_logging()
    log.info("=== 네이버 블로그 자동 댓글 시작 ===")
    log.info("대상 블로그: %s", config.BLOG_URL)
    log.info("목표 시각: %02d:%02d / 비밀댓글=%s", config.TARGET_HOUR, config.TARGET_MINUTE, config.SECRET_COMMENT)
    log.info("작성할 댓글:\n%s", config.COMMENT_TEXT)

    if not config.COMMENT_TEXT.strip():
        log.error(".env 의 COMMENT_TEXT 가 비어 있습니다.")
        return
    if not config.NAVER_ID or not config.NAVER_PW:
        log.error(".env 의 NAVER_ID / NAVER_PW 를 확인하세요.")
        return

    keep_awake(True)
    log_no = config.POST_LOGNO or None
    posted = False
    failures = 0
    driver = None

    try:
        while not posted:
            try:
                if driver is None:
                    driver = build_driver()
                    manual_window = int(max(1800, seconds_to_target()))
                    if not login(driver, max_wait_manual=manual_window):
                        log.error("로그인 실패. 60초 후 다시 시도합니다.")
                        time.sleep(60)
                        continue
                    if log_no is None:
                        log_no = resolve_target_logno(driver)
                    comment_mod.is_comment_enabled(driver, log_no)

                posted = run_once(driver, log_no)
                if posted:
                    log.info("=== 완료: 댓글 등록 성공. 브라우저 탭과 screenshots/ 로 확인하세요. ===")
                    break

                failures += 1
                log.error("댓글 등록 실패(%d회). 열린 탭에서 직접 등록을 검토하세요. 20초 후 재시도.", failures)
                if failures >= 3:
                    log.error("=== 3회 연속 실패. 종료합니다. 열린 탭에서 직접 등록하세요. ===")
                    break
                time.sleep(20)

            except KeyboardInterrupt:
                raise
            except Exception as e:
                log.exception("오류 발생, 재연결 후 계속합니다: %s", e)
                if driver is not None and not config.ATTACH_MODE:
                    try:
                        driver.quit()
                    except Exception:
                        pass
                driver = None  # attach 모드: Chrome 은 그대로 두고 세션만 다시 연결
                time.sleep(5)
    except KeyboardInterrupt:
        log.info("사용자 중단.")
    finally:
        keep_awake(False)
        if config.ATTACH_MODE:
            log.info("Attach 모드: 브라우저 탭은 그대로 둡니다.")
        elif driver is not None:
            time.sleep(10)
            log.info("브라우저 종료.")
            try:
                driver.quit()
            except Exception:
                pass


if __name__ == "__main__":
    main()
