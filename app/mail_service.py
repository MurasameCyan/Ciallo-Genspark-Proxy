"""Cloudflare Temp Email client and small mail parsing helpers."""

from __future__ import annotations

import json
import re
import time
import uuid
from collections.abc import Mapping
from email.utils import parseaddr
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

from .domain_pool import DomainPool

try:  # Keep importing the control plane possible in minimal environments.
    import requests
except ImportError:  # pragma: no cover - exercised only without optional dependency
    requests = None


_API_SUFFIXES = (
    "/admin/new_address",
    "/api/new_address",
    "/admin/create",
    "/api/mails",
    "/api/mail",
    "/admin",
    "/api",
)


def normalize_mail_api_base(raw: Any) -> str:
    """Normalize a worker URL to its origin/base path.

    Users commonly paste the full address-creation endpoint into the Base URL
    field.  Removing known API suffixes here keeps every request path stable.
    """
    value = str(raw or "").strip()
    if not value:
        return ""
    parts = urlsplit(value)
    path = parts.path.rstrip("/")
    lowered = path.lower()
    for suffix in sorted(_API_SUFFIXES, key=len, reverse=True):
        if lowered == suffix or lowered.endswith(suffix):
            path = path[: -len(suffix)].rstrip("/") or ""
            break
    if parts.scheme and parts.netloc:
        return urlunsplit((parts.scheme, parts.netloc, path, "", "")).rstrip("/")
    # urlsplit treats a bare worker hostname as a path.  Preserve it while
    # still applying the same suffix handling.
    return path or value.rstrip("/")


def build_cf_auth_headers(mode: Any, api_key: Any, content_type: bool = True) -> dict[str, str]:
    """Build Cloudflare worker authentication headers without logging secrets."""
    value = str(api_key or "")
    normalized = str(mode or "none").strip().lower().replace("_", "-")
    aliases = {"admin": "x-admin-auth", "api-key": "x-api-key", "public": "none"}
    normalized = aliases.get(normalized, normalized)
    if normalized not in {"none", "x-admin-auth", "bearer", "x-api-key", "query-key"}:
        raise ValueError(f"不支持的邮箱 API 认证模式: {mode}")
    headers: dict[str, str] = {"Content-Type": "application/json"} if content_type else {}
    if value:
        if normalized == "x-admin-auth":
            headers["x-admin-auth"] = value
        elif normalized == "bearer":
            headers["Authorization"] = f"Bearer {value}"
        elif normalized == "x-api-key":
            headers["X-API-Key"] = value
    return headers


_CODE_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9]{3}-[A-Za-z0-9]{3}(?![A-Za-z0-9])")
_SIX_DIGIT_RE = re.compile(r"(?<!\d)\d{6}(?!\d)")
_TEMPLATE_CODE = "177010"


def extract_verification_code(content: Any) -> str | None:
    """Extract a Genspark-style ``XXX-XXX`` or six-digit code."""
    if content is None:
        return None
    if isinstance(content, (Mapping, list, tuple)):
        try:
            content = json.dumps(content, ensure_ascii=False)
        except (TypeError, ValueError):
            content = str(content)
    text = str(content)
    match = _CODE_RE.search(text)
    if match:
        return match.group(0).upper()
    for candidate in _SIX_DIGIT_RE.findall(text):
        if candidate != _TEMPLATE_CODE:
            return candidate
    return None


def _config_value(config: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        value = config.get(key)
        if value not in (None, ""):
            return value
    nested = config.get("mail")
    if isinstance(nested, Mapping):
        for key in keys:
            value = nested.get(key)
            if value not in (None, ""):
                return value
    return default


def _looks_like_email(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    address = parseaddr(value)[1].strip()
    return bool(address and re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", address))


def _find_value(payload: Any, names: set[str]) -> Any:
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            if str(key).lower() in names and value not in (None, ""):
                return value
        for value in payload.values():
            found = _find_value(value, names)
            if found not in (None, ""):
                return found
    elif isinstance(payload, (list, tuple)):
        for value in payload:
            found = _find_value(value, names)
            if found not in (None, ""):
                return found
    return None


class CloudflareMailClient:
    """Small synchronous client for a Cloudflare Temp Email worker."""

    def __init__(self, config: Mapping[str, Any] | None, session: Any = None, pool: DomainPool | None = None, log=print):
        self.config = config if isinstance(config, Mapping) else {}
        self.session = session if session is not None else (requests.Session() if requests is not None else None)
        self.pool = pool if pool is not None else DomainPool.from_config(self.config)
        self.log = log or (lambda _message: None)
        self.base_url = normalize_mail_api_base(_config_value(self.config, "mail_api_base", default=""))
        self.auth_mode = self._auth_mode()
        self.auth_key = str(_config_value(self.config, "mail_admin_auth", "mail_api_key", default="") or "")

    def _auth_mode(self) -> str:
        configured = _config_value(self.config, "mail_auth_mode", "cloudflare_auth_mode", default=None)
        if configured is None:
            return "x-admin-auth" if _config_value(self.config, "mail_admin_auth", default="") else "none"
        value = str(configured).strip().lower().replace("_", "-")
        return {"admin": "x-admin-auth", "api-key": "x-api-key", "public": "none"}.get(value, value)

    def _require_base(self) -> str:
        if not self.base_url:
            raise RuntimeError("未配置邮箱 API 地址，请设置 mail_api_base")
        if self.session is None:
            raise RuntimeError("当前环境未安装 requests，或未注入 HTTP session")
        return self.base_url

    def _url(self, path: str) -> str:
        if path.startswith(("http://", "https://")):
            return path
        return f"{self._require_base().rstrip('/')}/{path.lstrip('/')}"

    def _request(self, method: str, url: str, *, headers: dict[str, str], json_body: Any = None, params: Mapping[str, Any] | None = None) -> Any:
        self._require_base()
        try:
            request = getattr(self.session, method.lower())
            kwargs: dict[str, Any] = {"headers": headers, "timeout": 30}
            if params:
                kwargs["params"] = dict(params)
            if json_body is not None:
                kwargs["json"] = json_body
            response = request(url, **kwargs)
        except Exception as exc:
            raise RuntimeError(f"邮箱 API 请求失败（{method.upper()} {url.split('?', 1)[0]}）: {exc}") from exc
        status = getattr(response, "status_code", 200)
        if status >= 400:
            raise RuntimeError(f"邮箱 API 返回 HTTP {status}（{method.upper()} {url.split('?', 1)[0]}）")
        try:
            response.raise_for_status()
        except AttributeError:
            pass
        except Exception as exc:
            raise RuntimeError(f"邮箱 API 请求失败（HTTP {status}）") from exc
        try:
            return response.json()
        except Exception as exc:
            raise RuntimeError("邮箱 API 返回了无法解析的 JSON") from exc

    def _headers(self, jwt: str | None = None, content_type: bool = False) -> dict[str, str]:
        headers = build_cf_auth_headers(self.auth_mode, self.auth_key, content_type=content_type)
        if jwt:
            # Mailbox JWT authenticates mailbox reads; retain worker auth headers
            # as well when the worker requires an admin key.
            headers["Authorization"] = f"Bearer {jwt}"
        return headers

    def _params(self, *, email: str = "") -> dict[str, str]:
        params: dict[str, str] = {}
        if email:
            params["email"] = email
        if self.auth_mode == "query-key" and self.auth_key:
            params["key"] = self.auth_key
        return params

    def create_temp_email(self) -> dict[str, str]:
        domain = self.pool.next_domain(_config_value(self.config, "mail_domain", default=""))
        if not domain:
            raise RuntimeError("未配置可用邮箱域名，请设置 mail_domain 或 mail_domains")
        custom_path = _config_value(self.config, "mail_create_path", default="")
        path = str(custom_path).strip() if custom_path else ("/api/new_address" if self.auth_mode == "none" else "/admin/new_address")
        payload = {"name": f"gs-{uuid.uuid4().hex[:12]}", "domain": domain, "enablePrefix": True}
        response = self._request("POST", self._url(path), headers=self._headers(content_type=True), json_body=payload, params=self._params())
        jwt = _find_value(response, {"jwt"})
        address = _find_value(response, {"email", "address"})
        if not jwt or not _looks_like_email(address):
            raise RuntimeError("邮箱 API 响应缺少有效的 jwt 或邮箱地址")
        address = parseaddr(str(address))[1]
        response_domain = address.rsplit("@", 1)[-1].lower()
        return {
            "address": address,
            "password": str(_find_value(response, {"password", "pass"}) or ""),
            "jwt": str(jwt),
            "domain": response_domain or domain,
        }

    @staticmethod
    def _message_list(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, Mapping)]
        if not isinstance(payload, Mapping):
            return []
        for key in ("mails", "messages", "emails", "items", "results", "data"):
            if key in payload:
                nested = CloudflareMailClient._message_list(payload[key])
                if nested:
                    return nested
        if any(key in payload for key in ("id", "messageId", "message_id", "subject", "content", "body")):
            return [dict(payload)]
        return []

    def fetch_emails(self, jwt: str, email: str = "") -> list[dict[str, Any]]:
        if not jwt:
            raise ValueError("缺少邮箱 JWT，无法读取邮件")
        payload = self._request("GET", self._url("/api/mails"), headers=self._headers(jwt), params=self._params(email=email))
        return self._message_list(payload)

    def fetch_email_detail(self, jwt: str, msg_id: Any, email: str = "") -> dict[str, Any] | Any:
        if not jwt:
            raise ValueError("缺少邮箱 JWT，无法读取邮件详情")
        if msg_id in (None, ""):
            raise ValueError("缺少邮件消息 ID")
        path = f"/api/mails/{quote(str(msg_id), safe='')}"
        return self._request("GET", self._url(path), headers=self._headers(jwt), params=self._params(email=email))

    def wait_for_code(self, jwt: str, email: str, timeout: float | None = None) -> str | None:
        if timeout is None:
            timeout = float(_config_value(self.config, "mail_timeout", default=240) or 240)
        timeout = max(0.0, float(timeout))
        interval = max(0.05, float(_config_value(self.config, "mail_poll_interval", default=2) or 2))
        deadline = time.monotonic() + timeout
        while True:
            messages = self.fetch_emails(jwt, email)
            for message in messages:
                code = extract_verification_code(message)
                if code:
                    return code
                msg_id = _find_value(message, {"id", "messageid", "message_id", "uid"})
                if msg_id not in (None, ""):
                    detail = self.fetch_email_detail(jwt, msg_id, email)
                    code = extract_verification_code(detail)
                    if code:
                        return code
            if time.monotonic() >= deadline:
                return None
            time.sleep(min(interval, max(0.0, deadline - time.monotonic())))


__all__ = [
    "CloudflareMailClient",
    "build_cf_auth_headers",
    "extract_verification_code",
    "normalize_mail_api_base",
]
