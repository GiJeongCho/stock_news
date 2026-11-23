"""네이버 댓글(cbox) API 직접 호출 — 브라우저 UI 없이 감지·등록.

실측(2026-11-04, PostView 의 u_cbox 가 보내는 요청을 캡처):
- objectId = "{blogNo}_201_{logNo}", groupId = blogNo, ticket=blog, templateId=default, pool=blogid
- 목록:  GET  web_naver_list_json.json   → 댓글 비활성 글이면 code=3300("댓글을 허용하지 않는 포스트"),
                                            활성 글이면 code=1000 + exposureConfig.status=COMMENT_ON  (약 50ms)
- 토큰:  GET  web_naver_token_json.json  → result.cbox_token (댓글 비활성 상태에서도 발급됨, 미리 받아둘 수 있음)
- 등록:  POST web_naver_create_json.json (form body, cbox_token 포함, secret=true 로 비밀댓글)
인증은 네이버 로그인 쿠키(NID_AUT/NID_SES 등)로 이뤄지며, Referer/Origin 은 blog.naver.com 이어야 한다.
"""
import logging
import re
import time
from urllib.parse import urlencode

import requests

import config

log = logging.getLogger(__name__)

API = "https://apis.naver.com/commentBox/cbox/"
SDK_VERSION = "4.20.18-release.20261027105730"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36")

CODE_OK = "1000"
CODE_COMMENT_CLOSED = "3300"


class CboxClient:
    def __init__(self, blog_id: str, log_no, blog_no: str | None = None, cookies: dict | None = None):
        self.blog_id = blog_id
        self.log_no = str(log_no)
        self.blog_no = blog_no or discover_blog_no(blog_id, log_no)
        self.object_id = f"{self.blog_no}_201_{self.log_no}"
        self.referer = config.post_view_url(self.log_no)
        self.token: str | None = None
        self.token_at = 0.0
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": UA,
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://blog.naver.com",
            "Referer": self.referer,
            "X-Cbox-Api-Sdk-Version": SDK_VERSION,
        })
        if cookies:
            self.set_cookies(cookies)

    # ── 인증 ──
    def set_cookies(self, cookies: dict):
        for k, v in cookies.items():
            self.s.cookies.set(k, v, domain=".naver.com")

    def logged_in(self) -> bool:
        return "NID_AUT" in self.s.cookies and "NID_SES" in self.s.cookies

    # ── 공통 파라미터 ──
    _TICKET = {"ticket": "blog", "templateId": "default", "pool": "blogid", "_cv": ""}

    def _body(self) -> dict:
        """POST body 공통부(ticket 류는 쿼리스트링으로만 보내야 함 — body 에 중복되면 'Wrong ticket')."""
        return {
            "lang": "ko", "pageType": "default", "country": "",
            "objectId": self.object_id, "categoryId": "", "pageSize": 50, "indexSize": 10,
            "groupId": self.blog_no, "listType": "OBJECT",
        }

    def _base(self) -> dict:
        d = dict(self._TICKET)
        d.update(self._body())
        return d

    # ── 상태 감지 ──
    def status(self, timeout: float = 3.0):
        """True=댓글 열림, False=닫힘, None=판정 불가(네트워크/기타)."""
        p = self._base()
        p.update(pageSize=1, indexSize=1, page=1, initialize="true", followSize=0,
                 userType="", useAltSort="true", replyPageSize=1, showReply="false")
        try:
            r = self.s.get(API + "web_naver_list_json.json", params=p, timeout=timeout)
            j = r.json()
        except Exception as e:
            log.debug("status 실패: %s", e)
            return None
        code = str(j.get("code"))
        if code == CODE_OK:
            st = ((j.get("result") or {}).get("exposureConfig") or {}).get("status")
            return st in (None, "COMMENT_ON")
        if code == CODE_COMMENT_CLOSED:
            return False
        log.debug("status 기타 응답: %s %s", code, j.get("message"))
        return None

    # ── 토큰 ──
    def fetch_token(self, timeout: float = 3.0) -> str | None:
        p = self._base()
        p.update(userType="")
        try:
            r = self.s.get(API + "web_naver_token_json.json", params=p, timeout=timeout)
            j = r.json()
            tok = (j.get("result") or {}).get("cbox_token")
            if tok:
                self.token, self.token_at = tok, time.time()
                return tok
            log.warning("토큰 응답에 cbox_token 없음: %s", r.text[:200])
        except Exception as e:
            log.warning("토큰 발급 실패: %s", e)
        return None

    def ensure_token(self, max_age: float = 60.0) -> str | None:
        if self.token and time.time() - self.token_at < max_age:
            return self.token
        return self.fetch_token() or self.token

    # ── 등록 ──
    def create(self, text: str, secret: bool = True, timeout: float = 4.0) -> tuple[bool, dict]:
        """댓글 등록. (성공여부, 응답json). 토큰이 없으면 즉석 발급."""
        tok = self.token or self.fetch_token()
        if not tok:
            return False, {"code": "no_token"}
        body = self._body()
        body.update({
            "clientType": "web-pc",
            "objectUrl": self.referer,
            "contents": text,
            "userType": "", "pick": "false", "manager": "false", "score": 0,
            "likeItId": f"{self.blog_id}_{self.log_no}",
            "sort": "NEW",
            "secret": "true" if secret else "false",
            "refresh": "true", "imageCount": 0, "commentType": "txt",
            "validateBanWords": "true", "invalidateCleanbotAlert": "false",
            "cbox_token": tok,
        })
        url = API + "web_naver_create_json.json?" + urlencode(self._TICKET)
        try:
            r = self.s.post(url, data=body, timeout=timeout,
                            headers={"Content-Type": "application/x-www-form-urlencoded"})
            j = r.json()
        except Exception as e:
            log.warning("create 요청 실패: %s", e)
            return False, {"code": "error", "message": str(e)}
        ok = str(j.get("code")) == CODE_OK and bool(j.get("success", True))
        return ok, j

    def delete(self, comment_no, timeout: float = 4.0) -> tuple[bool, dict]:
        """테스트용: 방금 단 댓글 삭제."""
        tok = self.ensure_token()
        body = self._body()
        body.update({"commentNo": comment_no, "userType": "", "clientType": "web-pc",
                     "pick": "false", "page": 1, "sort": "REVERSE_NEW", "cbox_token": tok})
        url = API + "web_naver_delete_json.json?" + urlencode(self._TICKET)
        try:
            r = self.s.post(url, data=body, timeout=timeout,
                            headers={"Content-Type": "application/x-www-form-urlencoded"})
            j = r.json()
            return str(j.get("code")) == CODE_OK, j
        except Exception as e:
            return False, {"code": "error", "message": str(e)}


_BLOGNO_RE = re.compile(r"var blogNo\s*=\s*'(\d+)'|\"blogNo\"\s*:\s*\"?(\d+)|blogNo\s*=\s*(\d+)")


def discover_blog_no(blog_id: str, log_no) -> str:
    """PostView HTML 에서 blogNo(숫자 id) 추출."""
    r = requests.get(config.post_view_url(log_no), headers={"User-Agent": UA}, timeout=10)
    r.raise_for_status()
    m = _BLOGNO_RE.search(r.text)
    if not m:
        raise RuntimeError("blogNo 를 찾지 못했습니다.")
    return next(g for g in m.groups() if g)


def cookies_from_driver(driver) -> dict:
    """현재 탭(naver.com 도메인)에서 쿠키 dict 추출."""
    out = {}
    for c in driver.get_cookies():
        if "naver.com" in (c.get("domain") or ""):
            out[c["name"]] = c["value"]
    return out
