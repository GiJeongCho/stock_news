"""Selenium Chrome 드라이버 생성."""
import logging
import os
import socket
import subprocess
import time

from selenium import webdriver
from selenium.common.exceptions import NoSuchWindowException
from selenium.webdriver.chrome.options import Options

import config

log = logging.getLogger(__name__)

_REAL_USER_DATA = os.path.normcase(
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data")
)


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _debug_user_data_dir() -> str:
    d = config.CHROME_DEBUG_USER_DATA
    if os.path.normcase(d) == _REAL_USER_DATA:
        log.warning(
            "Chrome 136+ 는 기본 User Data 폴더에서 원격 디버깅을 차단합니다. "
            "별도 폴더(.chrome-debug)로 대체합니다."
        )
        d = str(config.ROOT / ".chrome-debug")
    os.makedirs(d, exist_ok=True)
    return d


def _launch_debug_chrome():
    """원격 디버깅 포트가 열린 Chrome 을 띄운다(이미 떠 있으면 재사용)."""
    if _port_open(config.DEBUG_PORT):
        log.info("디버깅 포트 %d 이미 열려 있음 → 기존 Chrome 재사용.", config.DEBUG_PORT)
        return
    user_data = _debug_user_data_dir()
    cmd = [
        config.CHROME_PATH,
        f"--remote-debugging-port={config.DEBUG_PORT}",
        f"--user-data-dir={user_data}",
        f"--profile-directory={config.CHROME_PROFILE_NAME}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-session-crashed-bubble",
        "--start-maximized",
        config.NAVER_MAIN,
    ]
    log.info("디버깅 Chrome 실행: %s", " ".join(cmd))
    subprocess.Popen(cmd)
    for _ in range(60):
        if _port_open(config.DEBUG_PORT):
            log.info("디버깅 포트 %d 활성화 확인.", config.DEBUG_PORT)
            return
        time.sleep(0.5)
    raise RuntimeError(
        f"디버깅 포트 {config.DEBUG_PORT} 가 열리지 않았습니다. "
        f"같은 user-data-dir({user_data})로 이미 Chrome 이 떠 있는지 확인하세요."
    )


def safe_get(driver, url: str):
    """driver.get. 작업 탭이 사용자에 의해 닫혔으면 새 탭을 다시 열어 진행."""
    try:
        driver.get(url)
        return
    except NoSuchWindowException:
        handles = driver.window_handles
        if not handles:
            raise RuntimeError("Chrome 창이 모두 닫혔습니다. Chrome 을 종료하지 마세요.")
        driver.switch_to.window(handles[-1])
        driver.switch_to.new_window("tab")
        log.warning("작업 탭이 닫혀 있어 새 탭을 다시 열었습니다. (handle=%s)", driver.current_window_handle)
        driver.get(url)


def build_attached_driver() -> webdriver.Chrome:
    """디버깅 Chrome 에 연결하고, 작업용 새 탭을 연다."""
    _launch_debug_chrome()
    opts = Options()
    opts.add_experimental_option("debuggerAddress", f"127.0.0.1:{config.DEBUG_PORT}")
    driver = webdriver.Chrome(options=opts)
    driver.implicitly_wait(0)
    try:
        driver.switch_to.new_window("tab")
        log.info("새 탭을 열어 작업합니다. (handle=%s)", driver.current_window_handle)
    except Exception as e:
        log.warning("새 탭 열기 실패, 현재 탭 사용: %s", e)
    log.info("Chrome 세션에 연결 완료 (port=%d).", config.DEBUG_PORT)
    return driver


def build_driver() -> webdriver.Chrome:
    if config.ATTACH_MODE:
        return build_attached_driver()
    opts = Options()
    # 로그인 세션 유지를 위한 고정 프로필
    opts.add_argument(f"--user-data-dir={config.CHROME_USER_DATA_DIR}")
    # 봇 탐지 완화
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    opts.add_argument("--start-maximized")
    opts.add_argument("--no-first-run")
    opts.add_argument("--no-default-browser-check")
    # headless 는 캡차/탐지 때문에 비권장 → 사용 안 함

    driver = webdriver.Chrome(options=opts)
    # navigator.webdriver 숨기기
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"},
    )
    driver.implicitly_wait(0)
    log.info("Chrome 드라이버 생성 완료 (profile=%s)", config.CHROME_USER_DATA_DIR)
    return driver
