"""네이버 프리미엄콘텐츠 채널 목록/본문 수집 (로그인 쿠키 기반 requests).

실측(2026-10, insightstrader/insight):
- 목록: /contents → ul.content_list (data-cursor=lastContentId, data-has-next) > li.content_item
  · a.content_text_link[href=/.../contents/<id>] > strong.content_title
  · a.content_category, .content_author_text, .content_info_text(완독시간, 날짜), em.content_lock(잠김)
- 본문: /contents/<id> → og:title, .viewer_date_text("2026.10.07. 오전 7:42"),
  div.se-main-container (SmartEditor ONE: se-text / se-quotation / se-oglink / se-image / se-table ...)
  · 구독 중이면 .viewer_paywall_none 존재(페이월 없음)
"""
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import requests
from bs4 import BeautifulSoup

import config

log = logging.getLogger(__name__)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/154.0 Safari/537.36")


class AuthError(Exception):
    """로그인 세션이 유효하지 않음."""


@dataclass
class ListItem:
    content_id: str
    title: str
    category: str = ""
    author: str = ""
    date: str = ""
    read_time: str = ""
    locked: bool = False

    @property
    def url(self) -> str:
        return f"{config.CHANNEL_URL}/contents/{self.content_id}"


@dataclass
class Post:
    content_id: str
    title: str
    url: str
    category: str = ""
    author: str = ""
    published: str = ""           # "2026.10.07. 오전 7:42"
    body_lines: list = field(default_factory=list)
    links: list = field(default_factory=list)   # (title, url)
    image_count: int = 0
    paywalled: bool = False

    @property
    def body_text(self) -> str:
        return "\n".join(self.body_lines)


def build_session(cookies: dict) -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Referer": config.CHANNEL_URL, "Accept-Language": "ko-KR,ko;q=0.9"})
    for k, v in cookies.items():
        s.cookies.set(k, v, domain=".naver.com")
    return s


def _get(session: requests.Session, url: str) -> str:
    r = session.get(url, timeout=20, allow_redirects=True)
    if "nid.naver.com" in r.url:
        raise AuthError("로그인 페이지로 리다이렉트됨")
    r.raise_for_status()
    return r.text


def fetch_list(session: requests.Session, cursor: Optional[str] = None):
    """목록 1페이지(최신순 20개). 반환: (items, next_cursor or None)"""
    url = config.LIST_URL + (f"?lastContentId={cursor}" if cursor else "")
    html = _get(session, url)
    soup = BeautifulSoup(html, "lxml")
    ul = soup.select_one("ul.content_list")
    items = []
    for li in soup.select("li.content_item"):
        a = li.select_one("a.content_text_link[href*='/contents/']") or li.select_one("a[href*='/contents/']")
        if not a:
            continue
        m = re.search(r"/contents/(\w+)", a.get("href", ""))
        if not m:
            continue
        cid = m.group(1)
        title_el = li.select_one(".content_title")
        infos = [x.get_text(strip=True) for x in li.select(".content_info_text")]
        date = next((x for x in infos if re.match(r"\d{4}\.\d{2}\.\d{2}", x)), "")
        read_time = next((x for x in infos if "완독" in x), "")
        items.append(ListItem(
            content_id=cid,
            title=(title_el.get_text(strip=True) if title_el else a.get_text(strip=True)),
            category=(li.select_one(".content_category").get_text(strip=True) if li.select_one(".content_category") else ""),
            author=(li.select_one(".content_author_text").get_text(strip=True) if li.select_one(".content_author_text") else ""),
            date=date,
            read_time=read_time,
            locked=bool(li.select_one(".content_lock")),
        ))
    next_cursor = None
    if ul is not None and ul.get("data-has-next") == "true":
        next_cursor = ul.get("data-cursor") or None
    if not items:
        # 목록이 비어 있으면 세션 문제일 가능성이 큼
        if "로그인" in html and "content_list" not in html:
            raise AuthError("목록 페이지에서 콘텐츠를 찾지 못함(로그인 필요 추정)")
        log.warning("목록에서 콘텐츠를 찾지 못했습니다. (HTML %d bytes)", len(html))
    return items, next_cursor


_ZW = re.compile(r"[\u200b\ufeff]")


def _clean(s: str) -> str:
    s = _ZW.sub("", s)
    s = s.replace("\xa0", " ")
    return re.sub(r"[ \t]+", " ", s).strip()


def _extract_body(container) -> tuple[list, list, int]:
    """se-main-container 를 순서대로 훑어 텍스트 라인/링크/이미지 수 추출."""
    lines, links, images = [], [], 0
    comps = container.select(".se-component")
    if not comps:
        for p in container.find_all(["p", "li", "h1", "h2", "h3", "h4", "td"]):
            t = _clean(p.get_text(" ", strip=True))
            if t:
                lines.append(t)
        return lines, links, images

    for comp in comps:
        cls = set(comp.get("class", []))
        if "se-text" in cls or "se-sectionTitle" in cls:
            for p in comp.select("p, h1, h2, h3, h4, h5, li"):
                t = _clean(p.get_text(" ", strip=True))
                if t:
                    lines.append(t)
        elif "se-quotation" in cls:
            for p in comp.select(".se-quote p, .se-cite p, p"):
                t = _clean(p.get_text(" ", strip=True))
                if t:
                    lines.append(f"❝ {t}")
        elif "se-oglink" in cls:
            title_el = comp.select_one(".se-oglink-title")
            a = comp.select_one("a[href]")
            t = _clean(title_el.get_text(" ", strip=True)) if title_el else ""
            href = a["href"] if a else ""
            if t or href:
                links.append((t, href))
                lines.append(f"🔗 {t}".strip())
        elif "se-image" in cls or "se-imageStrip" in cls or "se-imageGroup" in cls:
            n = len(comp.select("img")) or 1
            images += n
            cap = comp.select_one(".se-caption")
            if cap and _clean(cap.get_text(" ", strip=True)):
                lines.append(f"[이미지] {_clean(cap.get_text(' ', strip=True))}")
            else:
                lines.append("[이미지]")
        elif "se-table" in cls:
            for tr in comp.select("tr"):
                cells = [_clean(td.get_text(" ", strip=True)) for td in tr.select("td, th")]
                cells = [c for c in cells if c]
                if cells:
                    lines.append(" | ".join(cells))
        elif "se-horizontalLine" in cls:
            lines.append("—")
        elif "se-video" in cls or "se-oembed" in cls:
            lines.append("[동영상]")
        else:
            t = _clean(comp.get_text(" ", strip=True))
            if t:
                lines.append(t)

    # 연속 중복/빈 줄 정리
    out = []
    for l in lines:
        if not l:
            continue
        if out and out[-1] == l:
            continue
        out.append(l)
    return out, links, images


def fetch_post(session: requests.Session, item: ListItem) -> Post:
    html = _get(session, item.url)
    soup = BeautifulSoup(html, "lxml")
    og = soup.find("meta", property="og:title")
    title = _clean(og["content"]) if og and og.get("content") else item.title
    date_el = soup.select_one(".viewer_date_text")
    published = _clean(date_el.get_text(" ", strip=True)) if date_el else item.date
    author_el = soup.select_one(".viewer_author_text, .viewer_author .author_name, .content_author_text")
    author = _clean(author_el.get_text(" ", strip=True)) if author_el else item.author

    post = Post(content_id=item.content_id, title=title, url=item.url,
                category=item.category, author=author, published=published)
    container = soup.select_one("div.se-main-container")
    paywall_none = soup.select_one(".viewer_paywall_none") is not None
    if container is None:
        post.paywalled = True
        desc = soup.find("meta", property="og:description")
        if desc and desc.get("content"):
            post.body_lines = [_clean(desc["content"])]
        log.warning("본문 컨테이너 없음(잠김/구독 필요?): %s", item.url)
        return post
    post.body_lines, post.links, post.image_count = _extract_body(container)
    post.paywalled = not paywall_none and len(post.body_text) < 300
    return post


def parse_published(published: str) -> Optional[datetime]:
    """'2026.10.07. 오전 7:42' → datetime"""
    m = re.match(r"(\d{4})\.(\d{2})\.(\d{2})\.?\s*(오전|오후)?\s*(\d{1,2}):(\d{2})", published or "")
    if not m:
        m2 = re.match(r"(\d{4})\.(\d{2})\.(\d{2})", published or "")
        return datetime(int(m2.group(1)), int(m2.group(2)), int(m2.group(3))) if m2 else None
    y, mo, d, ap, h, mi = m.groups()
    h = int(h)
    if ap == "오후" and h != 12:
        h += 12
    if ap == "오전" and h == 12:
        h = 0
    return datetime(int(y), int(mo), int(d), h, int(mi))


def content_id_time(cid: str) -> Optional[datetime]:
    """콘텐츠 ID 앞 12자리가 yymmddHHMMSS 형식."""
    m = re.match(r"(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})", cid)
    if not m:
        return None
    yy, mo, d, h, mi, s = map(int, m.groups())
    try:
        return datetime(2000 + yy, mo, d, h, mi, s)
    except ValueError:
        return None
