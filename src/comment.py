"""댓글 활성화 감지 및 작성 (네이버 블로그 PostView + u_cbox 실측 기준).

실측(2026-10, studiochoix):
- 댓글 활성 글: 본문 하단에 `a.btn_comment#Comi<logNo>` 존재. 클릭하면
  `#naverComment_201_<logNo>` 안에 u_cbox 가 렌더링됨.
- 댓글 비활성 글: `btn_comment` 자체가 없음.
- 입력창: `div.u_cbox_text[contenteditable=true]` (id: ..._write_textarea)
- 비밀댓글: `input.u_cbox_secret_check` (+ `label.u_cbox_secret_label`)
- 등록: `button.u_cbox_btn_upload`
- 비로그인 시 write box 에 `u_cbox_type_logged_out` 클래스.
"""
import logging
import time

from selenium.common.exceptions import NoAlertPresentException
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

import config
from browser import safe_get

log = logging.getLogger(__name__)

WRITE_BOX_SELECTORS = [
    "div.u_cbox_text[contenteditable='true']",
    "div.u_cbox_write_area div[contenteditable='true']",
    "textarea.u_cbox_text",
]
SUBMIT_SELECTORS = [
    "button.u_cbox_btn_upload",
    "a.u_cbox_btn_upload",
]
SECRET_CHECKBOX = "input.u_cbox_secret_check"
SECRET_LABEL = "label.u_cbox_secret_label"


def _find_first(driver, selectors, visible=True):
    for sel in selectors:
        try:
            for el in driver.find_elements(By.CSS_SELECTOR, sel):
                if not visible or el.is_displayed():
                    return el
        except Exception:
            continue
    return None


def _comment_buttons(driver, log_no):
    sels = [f"#Comi{log_no}", "a.btn_comment._cmtList", "a.btn_comment"]
    out = []
    for s in sels:
        try:
            out += driver.find_elements(By.CSS_SELECTOR, s)
        except Exception:
            pass
    return out


def load_post(driver, log_no, wait_sec: float = 0.0):
    """PostView 페이지 로드(iframe 없음)."""
    safe_get(driver, config.post_view_url(log_no))
    if wait_sec:
        time.sleep(wait_sec)


def comment_button_present(driver, log_no) -> bool:
    """현재 로드된 페이지에 댓글 버튼이 있는지(=댓글 기능 활성)."""
    return len(_comment_buttons(driver, log_no)) > 0


def is_comment_enabled(driver, log_no, reload: bool = True) -> bool:
    if reload:
        load_post(driver, log_no)
    present = comment_button_present(driver, log_no)
    log.info("댓글 버튼 존재=%s (logNo=%s)", present, log_no)
    return present


def open_comment_box(driver, log_no, timeout: float = 8.0):
    """댓글 버튼을 눌러 u_cbox 입력창을 띄우고 입력창 엘리먼트를 반환. 실패 시 None."""
    btns = _comment_buttons(driver, log_no)
    if not btns:
        log.info("댓글 버튼 없음 (아직 비활성).")
        return None
    btn = btns[0]
    try:
        driver.execute_script(
            "arguments[0].scrollIntoView({block:'center'}); arguments[0].click();", btn
        )
    except Exception as e:
        log.warning("댓글 버튼 클릭 실패: %s", e)
        return None

    deadline = time.time() + timeout
    while time.time() < deadline:
        box = _find_first(driver, WRITE_BOX_SELECTORS)
        if box:
            return box
        time.sleep(0.2)
    log.warning("댓글 입력창이 %.0f초 내에 나타나지 않음.", timeout)
    return None


def _logged_out_box(driver) -> bool:
    try:
        return bool(driver.find_elements(By.CSS_SELECTOR, ".u_cbox_write_box.u_cbox_type_logged_out"))
    except Exception:
        return False


def _ensure_secret(driver) -> bool:
    """비밀댓글 체크. 성공(체크됨) 여부 반환."""
    cb = _find_first(driver, [SECRET_CHECKBOX], visible=False)
    if cb is None:
        log.warning("비밀댓글 체크박스를 찾지 못함.")
        return False
    if cb.is_selected():
        return True
    # 체크박스 직접 클릭 → 라벨 클릭 → JS 클릭 순으로 시도
    label = _find_first(driver, [SECRET_LABEL])
    for action in (
        lambda: cb.click(),
        lambda: label.click() if label is not None else None,
        lambda: driver.execute_script("arguments[0].click();", cb),
    ):
        try:
            action()
        except Exception:
            continue
        time.sleep(0.2)
        if cb.is_selected():
            break
    ok = cb.is_selected()
    log.info("비밀댓글 체크 상태=%s", ok)
    return ok


def _focus_box(driver, box):
    """입력창 포커스. 비어 있을 때는 placeholder(.u_cbox_guide)가 위를 덮고 있어 그걸 클릭해야 한다."""
    guide = _find_first(driver, [".u_cbox_write_area .u_cbox_guide", ".u_cbox_guide"])
    try:
        if guide is not None:
            guide.click()
        else:
            box.click()
    except Exception:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'}); arguments[0].focus();", box)
        try:
            ActionChains(driver).move_to_element(box).click().perform()
        except Exception:
            pass
    time.sleep(0.15)


def _type_text(driver, box, text: str):
    _focus_box(driver, box)
    # 기존 내용 제거(재시도 시 중복 방지)
    try:
        box.send_keys(Keys.CONTROL, "a")
        box.send_keys(Keys.DELETE)
    except Exception:
        pass
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if line:
            box.send_keys(line)
        if i < len(lines) - 1:
            ActionChains(driver).key_down(Keys.SHIFT).send_keys(Keys.ENTER).key_up(Keys.SHIFT).perform()
        time.sleep(0.05)


def _accept_alert(driver):
    try:
        a = driver.switch_to.alert
        txt = a.text
        a.accept()
        log.warning("알림창 발생: %s", txt)
        return txt
    except NoAlertPresentException:
        return None
    except Exception:
        return None


def _box_cleared(driver, box, timeout: float = 4.0) -> bool:
    """등록 성공 시 u_cbox 가 입력창을 비운다. timeout 내 비워지면 True."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            cur = (box.get_attribute("innerText") or "").strip()
            if cur == "":
                return True
        except Exception:
            return True  # 입력창이 재생성되어 stale → 등록 후 리렌더링
        if _accept_alert(driver):
            return False
        time.sleep(0.25)
    return False


def _open_box_mobile(driver, log_no, timeout: float = 10.0):
    """모바일 댓글 모달(m.blog.naver.com/CommentList.naver)을 열어 입력창 반환. 버튼 클릭 불필요."""
    safe_get(driver, config.mobile_comment_url(log_no))
    deadline = time.time() + timeout
    while time.time() < deadline:
        box = _find_first(driver, WRITE_BOX_SELECTORS)
        if box:
            return box
        time.sleep(0.25)
    log.warning("모바일 댓글 입력창이 %.0f초 내에 나타나지 않음.", timeout)
    return None


def write_comment(driver, log_no, text: str, retries: int = 6) -> bool:
    """댓글 버튼 클릭 → 입력 → (비밀댓글) → 등록. 성공 시 True.

    1~3회차: PC PostView 경로(리허설 완료), 4회차부터: 모바일 댓글 모달 경로(폴백).
    """
    for attempt in range(1, retries + 1):
        use_mobile = attempt >= 4
        log.info("댓글 작성 시도 %d/%d (%s)", attempt, retries, "모바일" if use_mobile else "PC")
        try:
            if use_mobile:
                box = _open_box_mobile(driver, log_no)
            else:
                box = _find_first(driver, WRITE_BOX_SELECTORS)
                if box is None:
                    box = open_comment_box(driver, log_no)
                if box is None:
                    load_post(driver, log_no, wait_sec=1.0)
                    box = open_comment_box(driver, log_no)
            if box is None:
                log.warning("입력창을 찾지 못함. 재시도.")
                continue

            if _logged_out_box(driver):
                log.error("댓글창이 '비로그인' 상태입니다. 세션이 끊겼습니다.")
                return False

            _type_text(driver, box, text)
            if config.SECRET_COMMENT:
                _ensure_secret(driver)

            _screenshot(driver, f"before_submit_{log_no}_{attempt}")

            submit = _find_first(driver, SUBMIT_SELECTORS)
            if submit is None:
                log.warning("등록 버튼을 찾지 못함. 재시도.")
                continue
            driver.execute_script("arguments[0].click();", submit)
            if _box_cleared(driver, box):
                time.sleep(1.0)
                _screenshot(driver, f"after_submit_{log_no}_{attempt}")
                log.info("댓글 등록 성공으로 판정(입력창 비워짐).")
                return True

            # 알림창(로그인 필요/제한 등)이 떴거나 아직 등록되지 않음 → 한 번 더 등록 클릭
            log.warning("등록 반응 없음. 등록 버튼 재클릭.")
            submit = _find_first(driver, SUBMIT_SELECTORS)
            if submit is not None:
                driver.execute_script("arguments[0].click();", submit)
                if _box_cleared(driver, box):
                    time.sleep(1.0)
                    _screenshot(driver, f"after_submit_{log_no}_{attempt}")
                    log.info("댓글 등록 성공으로 판정(재클릭 후 입력창 비워짐).")
                    return True
            _screenshot(driver, f"submit_failed_{log_no}_{attempt}")
            log.warning("등록 실패로 판단. 페이지 재로드 후 재시도.")
            load_post(driver, log_no, wait_sec=1.0)
        except Exception as e:
            log.exception("댓글 작성 중 오류: %s", e)
            _accept_alert(driver)
            time.sleep(1.0)
            try:
                load_post(driver, log_no, wait_sec=1.0)
            except Exception:
                pass
    return False


def _screenshot(driver, name: str):
    try:
        path = config.SCREENSHOT_DIR / f"{name}.png"
        driver.save_screenshot(str(path))
        log.info("스크린샷 저장: %s", path)
    except Exception:
        pass
