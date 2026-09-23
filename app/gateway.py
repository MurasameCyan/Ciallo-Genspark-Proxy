from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterator

try:
    from curl_cffi import requests as http_requests
except ImportError:  # local development fallback
    import requests as http_requests  # type: ignore

from .accounts import AccountStore

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
)
UPSTREAM = "https://www.genspark.ai/api/agent/ask_proxy"
REFERER = "https://www.genspark.ai/agents?type=ai_chat"
MODELS = [
    "gpt-5", "gpt-5.1", "gpt-5.2", "gpt-5.4", "gpt-5.5", "gpt-5.6", "gpt-6",
    "gpt-5-pro", "gpt-5.1-high", "gpt-5.1-low", "gpt-5.1-medium", "gpt-5.4-mini",
    "gpt-5.4-nano", "gpt-5.4-pro", "gpt-5.5-pro", "gpt-5.6-luna", "gpt-5.6-sol",
    "gpt-5.6-terra", "gpt-6-luna", "gpt-6-sol", "claude-4-5-haiku", "claude-opus-4-6",
    "claude-opus-4-7", "claude-opus-4-8", "claude-opus-5", "claude-opus-5-5",
    "claude-sonnet-4", "claude-sonnet-4-5", "claude-sonnet-4-6", "claude-sonnet-5",
    "gemini-2.5-flash", "gemini-3.1-flash-lite-preview", "gemini-3.1-pro-preview",
    "gemini-3.6-flash", "gemini-3.7-flash", "gemini-3.8-flash", "GLM-5.3",
    "deep-seek-v4-flash", "deep-seek-v4.1-flash", "glm-5p3", "glm-5p3-flash-baseten",
    "grok-4.5", "grok-4.6", "grok-4.7", "kimi-k3", "minimax-m3", "nemotron-3-ultra",
]
ALIAS = {"claude-haiku-4-5": "claude-4-5-haiku", "claude-opus-4-5": "claude-opus-4-6"}


def _session():
    try:
        return http_requests.Session(impersonate="chrome")
    except TypeError:
        return http_requests.Session()


@dataclass
class Account:
    record: dict[str, Any]
    base_dir: str = ""
    cookie: str = ""
    cooldown_until: float = 0.0
    stats: dict[str, int] = field(default_factory=lambda: {"ok": 0, "fail": 0, "throttle": 0})

    def __post_init__(self) -> None:
        self.seq = self.record.get("seq")
        self.email = str(self.record.get("email") or "")
        self.proxy = str(self.record.get("proxy") or self.record.get("proxy_default") or os.environ.get("GS_PROXY") or "")
        self.load()

    @property
    def cookie_file(self) -> str:
        raw = str(self.record.get("cookie_file") or "")
        if raw and not os.path.isabs(raw) and self.base_dir:
            return os.path.join(self.base_dir, raw)
        return raw

    def load(self) -> None:
        path = self.cookie_file
        if not path or not os.path.exists(path):
            self.cookie = ""
            return
        try:
            data = json.load(open(path, encoding="utf-8"))
            self.cookie = "; ".join(f"{c['name']}={c['value']}" for c in data.get("cookies", []) if c.get("name"))
        except (OSError, ValueError, TypeError, KeyError):
            self.cookie = ""

    @property
    def ready(self) -> bool:
        return bool(self.cookie) and time.time() >= self.cooldown_until and self.record.get("status") != "disabled"

    def cooldown(self, seconds: float) -> None:
        self.cooldown_until = time.time() + max(0.0, seconds)

    def headers(self) -> dict[str, str]:
        rid = "|" + uuid.uuid4().hex + "." + uuid.uuid4().hex[:16]
        left, right = rid.lstrip("|").split(".")
        return {
            "User-Agent": UA,
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "Origin": "https://www.genspark.ai",
            "Referer": REFERER,
            "request-id": rid,
            "traceparent": f"00-{left}-{right}-01",
            "Cookie": self.cookie,
        }

    @property
    def proxies(self) -> dict[str, str] | None:
        return {"http": self.proxy, "https": self.proxy} if self.proxy else None


def build_body(payload: dict[str, Any]) -> dict[str, Any]:
    model = ALIAS.get(payload.get("model") or "claude-4-5-haiku", payload.get("model") or "claude-4-5-haiku")
    return {
        "ai_chat_model": model,
        "ai_chat_enable_search": False,
        "ai_chat_disable_personalization": False,
        "use_moa_proxy": False,
        "moa_models": [],
        "writingContent": None,
        "sas_ask_origin": "typed",
        "type": "ai_chat",
        "is_private": True,
        "messages": payload.get("messages") or [],
    }


def parse_sse(text: str) -> tuple[str, str, str | None, str | None]:
    content, deltas, throttle, error = None, [], None, None
    for line in text.splitlines():
        if not line.startswith("data: "):
            continue
        try:
            value = json.loads(line[6:])
        except (TypeError, ValueError):
            continue
        kind = value.get("type")
        if kind == "message_field" and value.get("field_name") == "content":
            content = value.get("field_value")
        elif kind == "message_field_delta" and value.get("field_name") == "content":
            deltas.append(value.get("delta") or "")
        elif kind == "message_result" and isinstance(value.get("message"), dict):
            text_value = value["message"].get("content") or ""
            if any(marker in text_value for marker in ("too quickly", "Rate limit", "积分已用完")):
                throttle = text_value[:200]
            elif not content:
                content = text_value
        elif kind == "error":
            error = json.dumps(value, ensure_ascii=False)[:300]
    return str(content or ""), "".join(deltas), throttle, error


class Gateway:
    def __init__(self, store: AccountStore):
        self.store = store
        self._lock = threading.RLock()
        self._rr = 0
        self.accounts: dict[int, Account] = {}
        self.reload()

    def reload(self) -> None:
        records = self.store.records()
        base_dir = str(self.store.path.parent)
        with self._lock:
            self.accounts = {int(r["seq"]): Account(r, base_dir=base_dir) for r in records if r.get("seq") is not None}

    def pick(self) -> Account | None:
        with self._lock:
            ready = [a for a in self.accounts.values() if a.ready]
            if not ready:
                return None
            account = ready[self._rr % len(ready)]
            self._rr += 1
            return account

    def health(self) -> dict[str, Any]:
        now = time.time()
        return {
            "accounts": [
                {
                    "seq": a.seq,
                    "email": a.email[:64],
                    "ready": a.ready,
                    "cooldown_left_s": max(0, round(a.cooldown_until - now)),
                    "stats": dict(a.stats),
                }
                for a in self.accounts.values()
            ]
        }

    def complete(self, payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
        model = payload.get("model") or "claude-4-5-haiku"
        body = build_body(payload)
        request_id = "chatcmpl-" + uuid.uuid4().hex[:24]
        created = int(time.time())
        last_error = "no_account"
        for _ in range(max(1, min(3, len(self.accounts)))):
            account = self.pick()
            if account is None:
                break
            try:
                response = _session().post(
                    UPSTREAM,
                    headers=account.headers(),
                    data=json.dumps(body),
                    proxies=account.proxies,
                    timeout=120,
                )
                raw = response.text
            except Exception as exc:  # network failures are account-local
                account.stats["fail"] += 1
                account.cooldown(30)
                last_error = f"{type(exc).__name__}: {exc}"
                continue
            if "not login" in raw.lower():
                account.stats["fail"] += 1
                account.cooldown(300)
                last_error = "not_login"
                continue
            if any(marker in raw for marker in ("Rate limit", "too quickly", "积分已用完")):
                account.stats["throttle"] += 1
                account.cooldown(3600)
                last_error = "rate_limit"
                continue
            content, joined, throttle, error = parse_sse(raw)
            if throttle or error:
                account.stats["throttle" if throttle else "fail"] += 1
                account.cooldown(60 if throttle else 30)
                last_error = throttle or error or "upstream_error"
                continue
            account.stats["ok"] += 1
            return {
                "id": request_id,
                "object": "chat.completion",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content or joined}}],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                "x_genspark": {"account": account.seq, "email": account.email[:32], "upstream_status": response.status_code},
            }, 200
        return {"error": {"message": f"所有账号都失败: {last_error}", "type": "no_account" if last_error == "no_account" else "upstream_error"}}, 429 if last_error == "no_account" else 502

    def stream(self, payload: dict[str, Any]) -> Iterator[str]:
        result, status = self.complete({**payload, "stream": False})
        if status != 200:
            yield f"data: {json.dumps(result, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
            return
        choice = result["choices"][0]
        yield f"data: {json.dumps({'id': result['id'], 'object': 'chat.completion.chunk', 'created': result['created'], 'model': result['model'], 'choices': [{'index': 0, 'delta': {'role': 'assistant'}, 'finish_reason': None}]}, ensure_ascii=False)}\n\n"
        text = choice["message"].get("content") or ""
        for offset in range(0, len(text), 160):
            delta = text[offset:offset + 160]
            yield f"data: {json.dumps({'id': result['id'], 'object': 'chat.completion.chunk', 'created': result['created'], 'model': result['model'], 'choices': [{'index': 0, 'delta': {'content': delta}, 'finish_reason': None}]}, ensure_ascii=False)}\n\n"
        yield f"data: {json.dumps({'id': result['id'], 'object': 'chat.completion.chunk', 'created': result['created'], 'model': result['model'], 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"
