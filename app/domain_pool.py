"""Domain selection helpers for temporary mailboxes."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any


_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$", re.IGNORECASE)


def _valid_domain(value: str) -> bool:
    """Return whether *value* looks like a DNS domain, not a URL/email."""
    if not value or len(value) > 253 or "." not in value:
        return False
    labels = value.split(".")
    return all(_LABEL_RE.fullmatch(label) for label in labels)


def parse_domains(raw: Any) -> list[str]:
    """Parse and normalize a domain list while retaining input order.

    The control plane accepts either a textarea-style string or a JSON list.
    Commas and newlines are separators in either form so values pasted from
    environment variables and the dashboard behave identically.
    """
    if raw is None:
        return []
    values: list[Any]
    if isinstance(raw, str):
        values = re.split(r"[,\r\n]+", raw)
    elif isinstance(raw, Sequence) and not isinstance(raw, (bytes, bytearray)):
        values = []
        for item in raw:
            if isinstance(item, str):
                values.extend(re.split(r"[,\r\n]+", item))
            else:
                values.append(item)
    else:
        values = [raw]

    result: list[str] = []
    seen: set[str] = set()
    for item in values:
        if not isinstance(item, str):
            continue
        domain = item.strip().lstrip("@").strip().lower().rstrip(".")
        if _valid_domain(domain) and domain not in seen:
            seen.add(domain)
            result.append(domain)
    return result
def _first_config_value(config: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = config.get(key)
        if value not in (None, "", [], ()):
            return value
    return None

def _configured_domains(config: Mapping[str, Any]) -> list[str]:
    """Return the first configured source that contains valid domains."""
    sources: list[Any] = [config.get("mail_domains"), config.get("mail_domain_pool")]
    nested = config.get("mail")
    if isinstance(nested, Mapping):
        sources.append(nested.get("domains"))
    sources.append(config.get("mail_domain"))
    for raw in sources:
        domains = parse_domains(raw)
        if domains:
            return domains
    return []


class DomainPool:
    """Select mailbox domains in round-robin or random order."""

    def __init__(self, domains: Sequence[str] | None = None, mode: str = "round_robin", rng: Any = None):
        import random

        self._rng = rng if rng is not None else random
        self.domains: list[str] = parse_domains(domains)
        self.mode = self._normalize_mode(mode)
        self._next_index = 0

    @staticmethod
    def _normalize_mode(mode: Any) -> str:
        value = str(mode or "round_robin").strip().lower().replace("-", "_")
        return value if value in {"round_robin", "random"} else "round_robin"

    @classmethod
    def from_config(cls, config: Mapping[str, Any] | None, rng: Any = None) -> "DomainPool":
        config = config if isinstance(config, Mapping) else {}
        domains = _configured_domains(config)
        nested = config.get("mail")
        mode = _first_config_value(config, "mail_domain_mode", "email_domain_mode")
        if mode is None and isinstance(nested, Mapping):
            mode = _first_config_value(nested, "domain_mode", "email_domain_mode")
        return cls(domains, mode or "round_robin", rng=rng)

    def update_config(self, config: Mapping[str, Any] | None) -> "DomainPool":
        """Reload domains without skipping the item due next in round-robin."""
        config = config if isinstance(config, Mapping) else {}
        old_next = self.domains[self._next_index % len(self.domains)] if self.domains else None
        domains = _configured_domains(config)
        nested = config.get("mail")
        mode = _first_config_value(config, "mail_domain_mode", "email_domain_mode")
        if mode is None and isinstance(nested, Mapping):
            mode = _first_config_value(nested, "domain_mode", "email_domain_mode")
        self.mode = self._normalize_mode(mode or self.mode)
        self.domains = domains
        if not self.domains:
            self._next_index = 0
        elif old_next in self.domains:
            self._next_index = self.domains.index(old_next)
        else:
            self._next_index %= len(self.domains)
        return self

    def next_domain(self, fallback: str = "") -> str:
        if not self.domains:
            return parse_domains(fallback)[0] if parse_domains(fallback) else str(fallback or "").strip().lstrip("@").lower()
        if self.mode == "random":
            return self._rng.choice(self.domains)
        value = self.domains[self._next_index]
        self._next_index = (self._next_index + 1) % len(self.domains)
        return value

    def list_domains(self) -> list[str]:
        return list(self.domains)

    def status(self) -> dict[str, Any]:
        next_domain = self.domains[self._next_index] if self.domains else ""
        return {
            "domains": self.list_domains(),
            "mode": self.mode,
            "count": len(self.domains),
            "next_domain": next_domain,
        }


__all__ = ["DomainPool", "parse_domains"]
