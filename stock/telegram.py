"""텔레그램 봇 전송 (HTML parse_mode, 4096자 분할, 다중 대상 브로드캐스트, chat_id 자동 탐지)."""
import logging
import re
import time

import requests

import config

log = logging.getLogger(__name__)

MAX_LEN = 4000  # 4096 한도에 여유


def _api(token: str, method: str) -> str:
    return f"https://api.telegram.org/bot{token}/{method}"


def discover_chat_id(token: str | None = None) -> str | None:
    """봇에게 메시지를 보낸 가장 최근 채팅의 chat_id."""
    token = token or config.TELEGRAM_BOT_TOKEN
    try:
        r = requests.get(_api(token, "getUpdates"), params={"timeout": 0}, timeout=15).json()
    except Exception as e:
        log.warning("getUpdates 실패: %s", e)
        return None
    if not r.get("ok"):
        log.warning("getUpdates 응답 오류: %s", r)
        return None
    chat_id = None
    for upd in r.get("result", []):
        msg = upd.get("message") or upd.get("edited_message") or upd.get("channel_post") or {}
        chat = msg.get("chat") or {}
        if chat.get("id"):
            chat_id = str(chat["id"])
            who = chat.get("username") or chat.get("title") or chat.get("first_name")
            log.info("chat_id 후보: %s (%s)", chat_id, who)
    return chat_id


def _split(text: str) -> list[str]:
    if len(text) <= MAX_LEN:
        return [text]
    chunks, cur = [], ""
    for para in text.split("\n"):
        if len(cur) + len(para) + 1 > MAX_LEN:
            if cur:
                chunks.append(cur)
            while len(para) > MAX_LEN:
                chunks.append(para[:MAX_LEN])
                para = para[MAX_LEN:]
            cur = para
        else:
            cur = f"{cur}\n{para}" if cur else para
    if cur:
        chunks.append(cur)
    return chunks


def _strip_html(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s)


def send_message(chat_id: str, text: str, token: str | None = None, retries: int = 3) -> bool:
    """한 대상에게 전송. 실패해도 예외를 던지지 않고 False 반환."""
    token = token or config.TELEGRAM_BOT_TOKEN
    ok_all = True
    for chunk in _split(text):
        sent = False
        for attempt in range(retries):
            try:
                r = requests.post(
                    _api(token, "sendMessage"),
                    json={"chat_id": chat_id, "text": chunk, "parse_mode": "HTML",
                          "disable_web_page_preview": True},
                    timeout=20,
                ).json()
                if r.get("ok"):
                    sent = True
                    break
                desc = r.get("description", "")
                log.warning("sendMessage 실패(chat=%s): %s", chat_id, desc)
                if "parse" in desc.lower() or "entity" in desc.lower():
                    # HTML 파싱 오류 → 태그 제거 후 평문 재전송
                    r2 = requests.post(_api(token, "sendMessage"),
                                       json={"chat_id": chat_id, "text": _strip_html(chunk),
                                             "disable_web_page_preview": True}, timeout=20).json()
                    if r2.get("ok"):
                        sent = True
                        break
                if r.get("error_code") == 429:
                    time.sleep(int(r.get("parameters", {}).get("retry_after", 3)) + 1)
                elif r.get("error_code") in (400, 403):
                    # chat not found / 봇을 시작하지 않음 등 → 재시도 무의미
                    break
                else:
                    time.sleep(2 * (attempt + 1))
            except Exception as e:
                log.warning("sendMessage 예외(chat=%s): %s", chat_id, e)
                time.sleep(2 * (attempt + 1))
        ok_all = ok_all and sent
        time.sleep(0.3)
    return ok_all


def targets(state: dict | None = None) -> list[dict]:
    """전송 대상 목록. TELEGRAM_TARGETS 가 있으면 그것, 없으면 단일 토큰/chat_id."""
    if config.TELEGRAM_TARGETS:
        return config.TELEGRAM_TARGETS
    chat_id = (state or {}).get("chat_id") or config.TELEGRAM_CHAT_ID
    if chat_id:
        return [{"name": chat_id, "token": config.TELEGRAM_BOT_TOKEN, "chat_id": chat_id}]
    return []


def broadcast(text: str, state: dict | None = None) -> dict[str, bool]:
    """모든 대상에게 전송. {이름: 성공여부}"""
    results = {}
    for t in targets(state):
        ok = send_message(t["chat_id"], text, token=t["token"])
        results[t["name"]] = ok
        if not ok:
            log.warning("→ %s(chat=%s) 전송 실패", t["name"], t["chat_id"])
    return results
