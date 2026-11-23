"""블로그 새 글(logNo) 감지."""
import logging
import re
import xml.etree.ElementTree as ET

import requests

import config

log = logging.getLogger(__name__)

_LOGNO_RE = re.compile(r"/(\d{6,})")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/154.0 Safari/537.36"
    )
}

_session = requests.Session()
_session.headers.update(HEADERS)


def comment_open_mobile_http(log_no):
    """모바일 글 HTML(m.blog.naver.com/<blogId>/<logNo>)의 commentEnable 플래그.

    실측: 댓글 비활성 글 → `"commentEnable":"false"`, 활성 글 → `"commentEnable":"true"`.
    실패 시 None.
    """
    try:
        resp = _session.get(config.mobile_post_url(log_no), timeout=8)
        if resp.status_code != 200:
            return None
        html = resp.text
        m = re.search(r'commentEnable["\']?\s*[:=]\s*["\']?(true|false)', html)
        if m:
            return m.group(1) == "true"
        return None
    except Exception as e:
        log.debug("모바일 HTTP 댓글 상태 확인 실패: %s", e)
        return None


def comment_open_desktop_http(log_no):
    """PC PostView HTML 에 댓글 버튼(id="Comi<logNo>")이 있으면 True.

    실측: 댓글 비활성 글에는 `btn_comment`/`Comi<logNo>` 가 아예 렌더링되지 않고,
    활성 글에는 서버 렌더링 HTML 에 포함된다. 실패(네트워크 등) 시 None.
    """
    try:
        resp = _session.get(config.post_view_url(log_no), timeout=8)
        if resp.status_code != 200:
            return None
        html = resp.text
        return (f'id="Comi{log_no}"' in html) or ("btn_comment" in html)
    except Exception as e:
        log.debug("PC HTTP 댓글 상태 확인 실패: %s", e)
        return None


def comment_open_http(log_no):
    """모바일 → PC 순으로 확인. 하나라도 True 면 True, 둘 다 실패면 None."""
    m = comment_open_mobile_http(log_no)
    if m is True:
        return True
    d = comment_open_desktop_http(log_no)
    if d is True:
        return True
    if m is None and d is None:
        return None
    return False


def _extract_logno(url: str):
    m = _LOGNO_RE.search(url or "")
    return int(m.group(1)) if m else None


def latest_logno_rss():
    """RSS에서 최신 글의 logNo(int)를 반환. 실패 시 None."""
    try:
        resp = requests.get(config.RSS_URL, headers=HEADERS, timeout=8)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        lognos = []
        for item in root.iter("item"):
            link_el = item.find("link")
            if link_el is not None and link_el.text:
                n = _extract_logno(link_el.text)
                if n:
                    lognos.append(n)
        if lognos:
            return max(lognos)
    except Exception as e:
        log.debug("RSS 파싱 실패: %s", e)
    return None


def latest_logno_browser(driver):
    """브라우저로 블로그에 접속해 현재 노출 글의 logNo를 추출(폴백)."""
    try:
        from browser import safe_get

        safe_get(driver, config.BLOG_URL)
        import time

        time.sleep(2)
        # mainFrame 의 src 에 logNo 가 들어있음
        from selenium.webdriver.common.by import By

        frame = driver.find_element(By.ID, "mainFrame")
        src = frame.get_attribute("src") or ""
        n = _extract_logno(src)
        if not n:
            m = re.search(r"logNo=(\d+)", src)
            n = int(m.group(1)) if m else None
        return n
    except Exception as e:
        log.debug("브라우저 logNo 추출 실패: %s", e)
        return None


def latest_logno(driver=None):
    """RSS 우선, 실패 시 브라우저 폴백."""
    n = latest_logno_rss()
    if n is None and driver is not None:
        n = latest_logno_browser(driver)
    return n
