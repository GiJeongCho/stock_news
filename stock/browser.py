"""원격 디버깅 Chrome 실행/연결 + 네이버 로그인 세션 관리.

Chrome 136+ 는 기본 User Data 폴더에서 원격 디버깅을 차단하므로 별도 폴더(.chrome-stock)를 쓴다.
한 번 로그인하면 그 폴더에 세션이 저장되어 이후 실행부터는 바로 로그인 상태다.
"""
import logging
import os
import socket
import subprocess
import time

import pyperclip
from selenium import webdriver
from selenium.common.exceptions import NoSuchWindowException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

import config

log = logging.getLogger(__name__)


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def launch_chrome():
    if _port_open(config.DEBUG_PORT):
        log.info("디버깅 포트 %d 이미 열려 있음 → 기존 Chrome 재사용.", config.DEBUG_PORT)
        return
    os.makedirs(config.CHROME_USER_DATA, exist_ok=True)
    cmd = [
        config.CHROME_PATH,
        f"--remote-debugging-port={config.DEBUG_PORT}",
        f"--user-data-dir={config.CHROME_USER_DATA}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-session-crashed-bubble",
        "--window-size=1200,900",
        config.NAVER_MAIN,
    ]
    log.info("Chrome 실행: %s", " ".join(cmd))
    subprocess.Popen(cmd)
    for _ in range(60):
        if _port_open(config.DEBUG_PORT):
            return
        time.sleep(0.5)
    raise RuntimeError(f"디버깅 포트 {config.DEBUG_PORT} 가 열리지 않았습니다.")


def connect() -> webdriver.Chrome:
    launch_chrome()
    opts = Options()
    opts.add_experimental_option("debuggerAddress", f"127.0.0.1:{config.DEBUG_PORT}")
    driver = webdriver.Chrome(options=opts)
    driver.implicitly_wait(0)
    try:
        driver.switch_to.new_window("tab")
    except Exception:
        pass
    log.info("Chrome 연결 완료 (port=%d).", config.DEBUG_PORT)
    return driver


def safe_get(driver, url: str):
    try:
        driver.get(url)
    except NoSuchWindowException:
        handles = driver.window_handles
        if not handles:
            raise RuntimeError("Chrome 창이 모두 닫혔습니다.")
        driver.switch_to.window(handles[-1])
        driver.switch_to.new_window("tab")
        log.warning("작업 탭이 닫혀 새 탭을 다시 열었습니다.")
        driver.get(url)


# ── 로그인 ──
def _auth_cookies(driver) -> dict:
    cookies = {c["name"]: c["value"] for c in driver.get_cookies()}
    return cookies


def is_logged_in(driver, navigate: bool = True) -> bool:
    if navigate:
        safe_get(driver, config.NAVER_MAIN)
        time.sleep(1.2)
    names = set(_auth_cookies(driver))
    ok = "NID_AUT" in names and "NID_SES" in names
    log.info("로그인 상태: %s", ok)
    return ok


_SET_VALUE_JS = """
const el = arguments[0], v = arguments[1];
const proto = Object.getPrototypeOf(el);
const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
setter.call(el, v);
el.dispatchEvent(new Event('input', {bubbles: true}));
el.dispatchEvent(new Event('change', {bubbles: true}));
"""


def _paste(el, text: str):
    """클립보드 붙여넣기 후, 값이 안 들어갔으면 React 식 setter + input 이벤트로 재시도.
    (2026.10 개편된 로그인 페이지는 붙여넣기만으로는 상태가 갱신되지 않음)"""
    el.click()
    try:
        pyperclip.copy(text)
        el.send_keys(Keys.CONTROL, "v")
        time.sleep(0.4)
    except Exception:
        pass
    if el.get_attribute("value") != text:
        el.clear()
        el.send_keys(text)
        time.sleep(0.2)
    # 프레임워크 상태 동기화를 위해 input/change 이벤트 강제 발생
    el.parent.execute_script(_SET_VALUE_JS, el, text)
    time.sleep(0.2)


def _dismiss_device_registration(driver):
    for by, sel in [(By.ID, "new.dontsave"), (By.XPATH, "//*[contains(text(),'등록안함')]")]:
        try:
            el = driver.find_element(by, sel)
            if el.is_displayed():
                el.click()
                time.sleep(1.5)
                return
        except Exception:
            continue


_LOGIN_BUTTONS = [
    (By.ID, "log.login"),             # 구 로그인 페이지
    (By.ID, "loginBtn_row"),          # 신 로그인 페이지(2026.10~)
    (By.ID, "loginBtn_column"),
    (By.CSS_SELECTOR, "button.btn_login"),
    (By.XPATH, "//button[normalize-space()='로그인']"),
]


def _click_login_button(driver) -> bool:
    """로그인 페이지 개편에 대비해 여러 후보 셀렉터를 순서대로 시도. 눌렀으면 True."""
    for by, sel in _LOGIN_BUTTONS:
        for el in driver.find_elements(by, sel):
            try:
                if not el.is_displayed():
                    continue
                driver.execute_script("arguments[0].click()", el)
                return True
            except Exception:
                continue
    return False


def login(driver, max_wait_manual: int = 900) -> bool:
    if is_logged_in(driver):
        return True
    safe_get(driver, config.LOGIN_URL)
    if config.AUTO_LOGIN and config.NAVER_ID and config.NAVER_PW:
        log.info("자동 로그인 시도 (ID=%s)", config.NAVER_ID)
        try:
            wait = WebDriverWait(driver, 15)
            id_input = wait.until(EC.presence_of_element_located((By.ID, "id")))
            pw_input = driver.find_element(By.ID, "pw")
            _paste(id_input, config.NAVER_ID)
            _paste(pw_input, config.NAVER_PW)
            if not _click_login_button(driver):
                pw_input.send_keys(Keys.ENTER)
        except Exception as e:
            log.warning("자동 입력 실패(%s). 창에서 직접 로그인해 주세요.", e)
    else:
        log.info("열린 Chrome 창에서 직접 로그인해 주세요 (ID=%s).", config.NAVER_ID)

    deadline = time.time() + max_wait_manual
    while time.time() < deadline:
        time.sleep(3)
        _dismiss_device_registration(driver)
        names = set(_auth_cookies(driver))
        if "NID_AUT" in names and "NID_SES" in names:
            log.info("로그인 성공.")
            return True
        log.warning("로그인 대기 중(캡차/추가인증이면 창에서 처리)...")
    log.error("로그인 시간 초과.")
    return False


def export_cookies(driver) -> dict:
    """requests 세션용 쿠키(dict). .naver.com 도메인 쿠키를 현재 탭 기준으로 가져온다."""
    return _auth_cookies(driver)
