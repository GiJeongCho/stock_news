"""네이버 로그인 및 세션 유지."""
import logging
import time

import pyperclip
from selenium.common.exceptions import StaleElementReferenceException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

import config
from browser import safe_get

log = logging.getLogger(__name__)


def is_logged_in(driver) -> bool:
    """네이버 로그인 세션 쿠키(NID_AUT/NID_SES) 존재로 로그인 상태 판정.

    page_source 의 'logout' 문자열은 비로그인 상태에서도 스크립트에 포함되어
    오탐하므로, 신뢰할 수 있는 인증 쿠키로 판정한다.
    """
    safe_get(driver, config.NAVER_MAIN)
    time.sleep(1.5)
    names = {c.get("name") for c in driver.get_cookies()}
    has_auth = "NID_AUT" in names and "NID_SES" in names
    log.info("로그인 쿠키 확인: NID_AUT/NID_SES 존재=%s", has_auth)
    return has_auth


_SET_VALUE_JS = """
const el = arguments[0], v = arguments[1];
const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el), 'value').set;
setter.call(el, v);
el.dispatchEvent(new Event('input', {bubbles: true}));
el.dispatchEvent(new Event('change', {bubbles: true}));
"""

_LOGIN_BUTTONS = [
    (By.ID, "log.login"),       # 구 로그인 페이지
    (By.ID, "loginBtn_row"),    # 신 로그인 페이지(2026.10~)
    (By.ID, "loginBtn_column"),
    (By.CSS_SELECTOR, "button.btn_login"),
    (By.XPATH, "//button[normalize-space()='로그인']"),
]


def _paste(element, text: str):
    """클립보드 붙여넣기 → 값이 안 들어갔으면 직접 입력 → input/change 이벤트 강제 발생.
    (2026.10 개편된 로그인 페이지는 붙여넣기만으로는 상태가 갱신되지 않음)"""
    element.click()
    try:
        pyperclip.copy(text)
        element.send_keys(Keys.CONTROL, "v")
        time.sleep(0.4)
    except Exception:
        pass
    if element.get_attribute("value") != text:
        element.clear()
        element.send_keys(text)
        time.sleep(0.2)
    element.parent.execute_script(_SET_VALUE_JS, element, text)
    time.sleep(0.2)


def _click_login_button(driver) -> bool:
    for by, sel in _LOGIN_BUTTONS:
        for el in driver.find_elements(by, sel):
            try:
                if el.is_displayed():
                    driver.execute_script("arguments[0].click()", el)
                    return True
            except Exception:
                continue
    return False


def login(driver, max_wait_manual: int = 300) -> bool:
    """로그인 수행. 이미 로그인되어 있으면 통과.
    캡차/추가인증이 뜨면 사람이 처리할 때까지 대기한다.
    """
    if is_logged_in(driver):
        log.info("이미 로그인된 세션입니다.")
        return True

    safe_get(driver, config.LOGIN_URL)

    if config.AUTO_LOGIN:
        log.info("자동 로그인을 시도합니다.")
        wait = WebDriverWait(driver, 15)
        wait.until(EC.presence_of_element_located((By.ID, "id")))
        time.sleep(1.5)  # 신 로그인 페이지는 로드 직후 재렌더링됨(stale 방지)
        for _ in range(3):
            try:
                _paste(driver.find_element(By.ID, "id"), config.NAVER_ID)
                pw_input = driver.find_element(By.ID, "pw")
                _paste(pw_input, config.NAVER_PW)
                break
            except StaleElementReferenceException:
                time.sleep(1.0)

        if not _click_login_button(driver):
            pw_input.send_keys(Keys.ENTER)
        time.sleep(3)
    else:
        log.info(
            "열린 Chrome 창의 네이버 로그인 페이지에서 직접 로그인해 주세요 (ID: %s). "
            "로그인되면 자동으로 다음 단계로 진행합니다. (한 번 로그인하면 이후 실행부터는 유지됩니다)",
            config.NAVER_ID,
        )

    # 캡차/추가인증/기기등록 화면 대응: 로그인 완료될 때까지 대기
    deadline = time.time() + max_wait_manual
    while time.time() < deadline:
        _dismiss_device_registration(driver)
        if _has_auth_cookie(driver):
            log.info("로그인 성공.")
            return True
        log.warning("로그인 대기 중... 열린 Chrome 창에서 로그인해 주세요.")
        time.sleep(5)

    log.error("제한 시간 내 로그인 실패.")
    return False


def _has_auth_cookie(driver) -> bool:
    try:
        names = {c.get("name") for c in driver.get_cookies()}
        return "NID_AUT" in names and "NID_SES" in names
    except Exception:
        return False


def _dismiss_device_registration(driver):
    """로그인 직후 '새로운 기기 등록' 화면이 뜨면 '등록안함'을 눌러 통과."""
    for by, sel in [
        (By.ID, "new.dontsave"),
        (By.CSS_SELECTOR, "a#new\\.dontsave"),
        (By.XPATH, "//*[contains(text(),'등록안함')]"),
    ]:
        try:
            el = driver.find_element(by, sel)
            if el.is_displayed():
                el.click()
                log.info("기기 등록 화면: '등록안함' 클릭.")
                time.sleep(1.5)
                return
        except Exception:
            continue
