from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from threading import RLock
from typing import Any

from .config_store import DATA_DIR, ROOT_DIR


class AccountStore:
    def __init__(self, path: Path | str | None = None):
        raw = path or os.environ.get("GS_ACCOUNTS") or DATA_DIR / "accounts.json"
        self.path = Path(raw).expanduser()
        if not self.path.is_absolute():
            self.path = ROOT_DIR / self.path
        self._lock = RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _read(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {"channel": "genspark", "accounts": []}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(value, dict) and isinstance(value.get("accounts"), list):
                return value
        except (OSError, ValueError):
            pass
        return {"channel": "genspark", "accounts": []}

    def records(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self._read().get("accounts", []) if isinstance(item, dict)]

    def next_seq(self) -> int:
        seqs = []
        for item in self.records():
            try:
                seqs.append(int(item.get("seq")))
            except (TypeError, ValueError):
                continue
        return max(seqs, default=0) + 1

    def upsert(self, record: dict[str, Any]) -> dict[str, Any]:
        try:
            seq = int(record["seq"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("账号 seq 必须是整数") from exc
        with self._lock:
            doc = self._read()
            accounts = [item for item in doc.get("accounts", []) if int(item.get("seq", -1)) != seq]
            accounts.append({**record, "seq": seq})
            accounts.sort(key=lambda item: int(item.get("seq", 0)))
            doc["accounts"] = accounts
            self._atomic_write(doc)
            return dict(record, seq=seq)

    def delete(self, seq: int) -> bool:
        with self._lock:
            doc = self._read()
            before = len(doc.get("accounts", []))
            doc["accounts"] = [item for item in doc.get("accounts", []) if int(item.get("seq", -1)) != int(seq)]
            if len(doc["accounts"]) == before:
                return False
            self._atomic_write(doc)
            return True

    def public_records(self, gateway_accounts: dict[int, Any] | None = None) -> list[dict[str, Any]]:
        gateway_accounts = gateway_accounts or {}
        result = []
        for record in self.records():
            seq = int(record.get("seq", 0))
            account = gateway_accounts.get(seq)
            result.append(
                {
                    "seq": seq,
                    "email": str(record.get("email") or ""),
                    "status": str(record.get("status") or "active"),
                    "cookie_file": str(record.get("cookie_file") or ""),
                    "proxy": str(record.get("proxy") or record.get("proxy_default") or ""),
                    "ready": bool(account and account.ready),
                    "stats": dict(account.stats) if account else {"ok": 0, "fail": 0, "throttle": 0},
                }
            )
        return result

    def _atomic_write(self, value: dict[str, Any]) -> None:
        fd, tmp_name = tempfile.mkstemp(prefix="accounts.", suffix=".tmp", dir=str(self.path.parent))
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
