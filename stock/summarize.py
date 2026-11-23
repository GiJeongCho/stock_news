"""글 요약 → 텔레그램 메시지 텍스트(HTML parse_mode) 생성.

- LLM 키(OPENAI/ANTHROPIC)가 있으면 LLM 요약, 없으면 규칙 기반 요약.
- 규칙 기반: 면책 문구 제거 → 짧은 글은 전문, 긴 글은 핵심 라인(✅/▶/📌/숫자·가격·매매 키워드) 우선 추출.
"""
import html
import logging
import re

import config
from premium import Post

log = logging.getLogger(__name__)

DISCLAIMER_MARKERS = ("[투자 유의사항 안내]", "투자 유의사항", "본 채널에서 제공하는 모든 정보는 투자 판단을 위한 참고")
KEY_PREFIX = ("✅", "▶", "📌", "■", "●", "◆", "★", "☑", "✔", "①", "②", "③", "④", "⑤", "<", "[", "※", "*", "-")
KEY_WORDS = re.compile(
    r"(매수|매도|편입|편출|비중|홀딩|보유|목표|TP|손절|진입|분할|신규|포트|계획|시나리오|돌파|지지|저항|"
    r"코스피|코스닥|나스닥|S&P|환율|금리|실적|\d+(?:,\d{3})*(?:\.\d+)?\s*(?:원|%|달러|p|포인트))",
    re.I,
)


def strip_disclaimer(lines: list[str]) -> list[str]:
    out = []
    for l in lines:
        if any(m in l for m in DISCLAIMER_MARKERS):
            break
        out.append(l)
    # 뒤쪽에 남은 면책 번호 항목 제거
    while out and re.match(r"^\d\.\s*(본 채널|당사는|모든 투자의)", out[-1]):
        out.pop()
    return out


TIER1_PREFIX = ("✅", "📌", "❝", "■", "★", "※", "<", "[")   # 소제목·결론·인용(요약문)
TIER2_PREFIX = ("▶", "●", "◆", "☑", "✔", "①", "②", "③", "④", "⑤", "*", "-", "·")


def _tier(l: str) -> int:
    if l.startswith(TIER1_PREFIX):
        return 1
    if l.startswith(TIER2_PREFIX) or KEY_WORDS.search(l):
        return 2
    return 3


def extractive_summary(post: Post, budget: int) -> tuple[str, bool]:
    """반환: (요약 텍스트, 생략 여부)

    짧은 글은 전문. 긴 글은 1단계(소제목/결론/인용) → 2단계(세부 포인트·수치) → 3단계(나머지)
    순으로 예산을 채우되, 원문 순서를 유지한다.
    """
    lines = strip_disclaimer(post.body_lines)
    lines = [l for l in lines if l not in ("—",) and not l.startswith("[이미지]")]
    full = "\n".join(lines)
    if len(full) <= budget:
        return full, False

    chosen: dict[int, str] = {}
    used = 0
    for tier in (1, 2, 3):
        for i, l in enumerate(lines):
            if i in chosen or _tier(l) != tier:
                continue
            if used + len(l) + 1 > budget:
                continue
            chosen[i] = l
            used += len(l) + 1
        if used >= budget * 0.9:
            break
    text = "\n".join(chosen[i] for i in sorted(chosen))
    if not text:
        text = full[:budget]
    return text, True


def _clean_category(c: str) -> str:
    c = re.sub(r"[［\[]", "", c)
    c = re.sub(r"[］\]]", " ", c)
    return re.sub(r"\s+", " ", c).strip()


def llm_summary(post: Post) -> str | None:
    body = "\n".join(strip_disclaimer(post.body_lines))[:12000]
    prompt = (
        "다음은 한국 주식 유료 채널의 글이다. 투자자가 1분 안에 파악할 수 있도록 한국어로 요약하라.\n"
        "- 핵심 결론/시장 뷰 1~2줄\n- 종목별 액션(매수/매도/목표가/비중/보유)은 빠짐없이 불릿으로\n"
        "- 수치(가격, %, 지수)는 그대로 유지\n- 과장/미사여구 제거, 800자 이내\n\n"
        f"제목: {post.title}\n\n{body}"
    )
    try:
        if config.OPENAI_API_KEY:
            from openai import OpenAI
            client = OpenAI(api_key=config.OPENAI_API_KEY)
            r = client.chat.completions.create(
                model=config.OPENAI_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
            )
            return r.choices[0].message.content.strip()
        if config.ANTHROPIC_API_KEY:
            import anthropic
            client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
            r = client.messages.create(
                model=config.ANTHROPIC_MODEL, max_tokens=1200,
                messages=[{"role": "user", "content": prompt}],
            )
            return "".join(getattr(b, "text", "") for b in r.content).strip()
    except Exception as e:
        log.warning("LLM 요약 실패, 규칙 기반으로 대체: %s", e)
    return None


def _esc(s: str) -> str:
    return html.escape(s, quote=False)


def build_message(post: Post) -> str:
    """텔레그램 HTML 메시지."""
    head = f"<b>{_esc(post.title)}</b>"
    meta = " · ".join(x for x in [_clean_category(post.category), post.author, post.published] if x)
    parts = [head]
    if meta:
        parts.append(f"<i>{_esc(meta)}</i>")

    if post.paywalled:
        parts.append("⚠️ 본문이 잠겨 있습니다(구독/로그인 확인 필요).")
        if post.body_lines:
            parts.append(_esc(post.body_text[: config.SUMMARY_MAX_CHARS]))
    else:
        summary = None
        if config.OPENAI_API_KEY or config.ANTHROPIC_API_KEY:
            summary = llm_summary(post)
        if summary:
            parts.append(_esc(summary))
        else:
            text, truncated = extractive_summary(post, config.SUMMARY_MAX_CHARS)
            parts.append(_esc(text))
            if truncated:
                parts.append(f"<i>…핵심만 발췌 (원문 {len(post.body_text):,}자)</i>")
    if post.links:
        shown = [f"• <a href=\"{_esc(u)}\">{_esc(t or u)}</a>" for t, u in post.links[:5] if u]
        if shown:
            parts.append("관련 링크\n" + "\n".join(shown))
    parts.append(f"<a href=\"{_esc(post.url)}\">원문 보기</a>")
    return "\n\n".join(parts)


def build_digest(posts: list[Post], day_label: str) -> str:
    lines = [f"<b>📋 {_esc(day_label)} 올라온 글 {len(posts)}건</b>"]
    for p in posts:
        t = re.sub(r"^\d{4}\.\d{2}\.\d{2}\.?\s*", "", p.published or "")
        lines.append(f"• {_esc(t)} <a href=\"{_esc(p.url)}\">{_esc(p.title)}</a>" + (f" <i>({_esc(_clean_category(p.category))})</i>" if p.category else ""))
    return "\n".join(lines)
