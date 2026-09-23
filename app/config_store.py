from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from threading import RLock
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DATA_DIR") or ROOT_DIR / "data").expanduser()
CONFIG_FILE = Path(os.environ.get("GS_CONFIG") or DATA_DIR / "config.json")

DEFAULT_CONFIG: dict[str, Any] = {
    "port": int(os.environ.get("GS_PORT") or 8899),
    "bind_host": os.environ.get("GS_BIND_HOST") or "0.0.0.0",
    "accounts_file": os.environ.get("GS_ACCOUNTS") or str(DATA_DIR / "accounts.json"),
    "mail_api_base": os.environ.get("MAIL_API_BASE") or "",
    "mail_admin_auth": os.environ.get("MAIL_ADMIN_AUTH") or "",
    "mail_domain": os.environ.get("MAIL_DOMAIN") or "",
    "mail_domains": os.environ.get("MAIL_DOMAINS") or "",
    "mail_domain_mode": os.environ.get("MAIL_DOMAIN_MODE") or "round_robin",
    "mail_auth_mode": os.environ.get("MAIL_AUTH_MODE") or "x-admin-auth",
    "mail_create_path": os.environ.get("MAIL_CREATE_PATH") or "",
    "mail_poll_interval": float(os.environ.get("MAIL_POLL_INTERVAL") or 2),
    "mail_timeout": int(os.environ.get("MAIL_TIMEOUT") or 240),
    "register_auto_solve": os.environ.get("REGISTER_AUTO_SOLVE", "1") not in {"0", "false", "no"},
    "register_password": os.environ.get("REGISTER_PASSWORD") or "",
    "twocaptcha_key": os.environ.get("TWOCAPTCHA_KEY") or "",
    "twocaptcha_proxy": os.environ.get("TWOCAPTCHA_PROXY") or "",
}

_ALLOWED = {
    "mail_api_base",
    "mail_admin_auth",
    "mail_domain",
    "mail_domains",
    "mail_domain_mode",
    "mail_auth_mode",
    "mail_create_path",
    "mail_poll_interval",
    "mail_timeout",
    "register_auto_solve",
    "register_password",
    "twocaptcha_key",
    "twocaptcha_proxy",
}


class ConfigStore:
    def __init__(self, path: Path | str = CONFIG_FILE):
        self.path = Path(path)
        self._lock = RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict[str, Any]:
        with self._lock:
            saved: dict[str, Any] = {}
            try:
                if self.path.is_file():
                    raw = json.loads(self.path.read_text(encoding="utf-8"))
                    if isinstance(raw, dict):
                        saved = raw
            except (OSError, ValueError):
                saved = {}
            merged = dict(DEFAULT_CONFIG)
            merged.update(saved)
            return merged

    def save(self, patch: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(patch, dict):
            raise ValueError("配置必须是 JSON 对象")
        with self._lock:
            current = self.load()
            for key in _ALLOWED:
                if key not in patch:
                    continue
                value = patch[key]
                if key in {"mail_api_base", "mail_domain", "mail_auth_mode", "mail_create_path", "mail_domain_mode", "register_password", "twocaptcha_key", "twocaptcha_proxy"}:
                    value = str(value or "").strip()
                elif key == "mail_domains":
                    if isinstance(value, list):
                        value = [str(item).strip() for item in value if str(item).strip()]
                    else:
                        value = str(value or "").strip()
                elif key == "mail_poll_interval":
                    value = max(0.5, min(60.0, float(value)))
                elif key == "mail_timeout":
                    value = max(30, min(1800, int(value)))
                elif key == "register_auto_solve":
                    if isinstance(value, str):
                        value = value.strip().lower() not in {"0", "false", "no", "off"}
                    else:
                        value = bool(value)
                if key in {"mail_admin_auth", "register_password", "twocaptcha_key"} and str(value).strip() in {"", "********", "(已设置)"}:
                    continue
                current[key] = value
            self._atomic_write(current)
            return current

    def public(self) -> dict[str, Any]:
        value = self.load()
        for secret_key in ("mail_admin_auth", "register_password", "twocaptcha_key"):
            value[f"{secret_key}_set"] = bool(str(value.get(secret_key) or ""))
            value[secret_key] = ""
        value.pop("accounts_file", None)
        return value

    def _atomic_write(self, value: dict[str, Any]) -> None:
        fd, tmp_name = tempfile.mkstemp(prefix="config.", suffix=".tmp", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(value, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, self.path)
        finally:
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass
