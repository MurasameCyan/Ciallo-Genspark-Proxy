from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any, Callable

from .config_store import ROOT_DIR

_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$", re.IGNORECASE)
_PLACEHOLDERS = {"unknown", "null", "none", "n/a"}
DEFAULT_REPO = "MurasameCyan/Ciallo-Genspark-Proxy"
DEFAULT_REF = "main"
_ENV_KEYS = ("GIT_COMMIT", "GITHUB_SHA", "SOURCE_COMMIT", "COMMIT_SHA", "BUILD_ID")

_CACHED_BUILD: str | None = None


def repo_slug() -> str:
    """Public repository to compare against. GITHUB_REPO lets forks point at their own."""
    raw = str(os.environ.get("GITHUB_REPO") or "").strip()
    if not raw:
        return DEFAULT_REPO
    cleaned = re.sub(r"^https?://github\.com/", "", raw, flags=re.IGNORECASE).rstrip("/")
    return cleaned or DEFAULT_REPO


def track_ref() -> str:
    """Branch the latest image is built from."""
    return str(os.environ.get("GITHUB_TRACK_REF") or "").strip() or DEFAULT_REF


def short_sha(raw: Any) -> str:
    """40-char hash -> 7 chars; placeholders -> empty; anything else truncated."""
    value = str(raw or "").strip().split()[0] if str(raw or "").strip() else ""
    if not value or value.lower() in _PLACEHOLDERS:
        return ""
    return value[:7].lower() if _SHA_RE.match(value) else value[:32]


def _from_env() -> str:
    for key in _ENV_KEYS:
        found = short_sha(os.environ.get(key))
        if found:
            return found
    return ""


def _from_git() -> str:
    try:
        return short_sha(
            subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=str(ROOT_DIR),
                capture_output=True,
                text=True,
                timeout=1.5,
            ).stdout
        )
    except (OSError, subprocess.SubprocessError):
        return ""


def build_id() -> str:
    """Short hash of this build, or 'unknown'. Resolved once (git is synchronous)."""
    global _CACHED_BUILD
    if _CACHED_BUILD is None:
        _CACHED_BUILD = _from_env() or _from_git() or "unknown"
    return _CACHED_BUILD


def build_info() -> dict[str, Any]:
    build = build_id()
    repo_url = f"https://github.com/{repo_slug()}"
    # An unparseable build (unknown, or a tag was injected) links to the branch
    # history rather than a 404 /commit/unknown.
    return {
        "build": build,
        "buildUrl": f"{repo_url}/commit/{build}" if _SHA_RE.match(build) else f"{repo_url}/commits/{track_ref()}",
        "repoUrl": repo_url,
        "trackRef": track_ref(),
    }


def _default_fetcher(url: str) -> Any:
    import requests

    return requests.get(
        url,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "ciallo-genspark-proxy"},
        timeout=10,
    )


def check_update(fetcher: Callable[[str], Any] | None = None) -> dict[str, Any]:
    """Compare this build with the tracked branch HEAD.

    Never raises: the reason lands in `error` so the dashboard can show it, and a
    failed check must not turn a button into a 500.
    """
    info = build_info()
    base: dict[str, Any] = {
        "current": info["build"],
        "latest": None,
        "hasUpdate": False,
        "htmlUrl": f"{info['repoUrl']}/commits/{info['trackRef']}",
        "publishedAt": None,
        "error": None,
    }
    url = f"https://api.github.com/repos/{repo_slug()}/commits/{info['trackRef']}"
    call = fetcher or _default_fetcher

    try:
        response = call(url)
    except Exception as exc:  # network errors, DNS, timeouts
        return {**base, "error": str(exc) or exc.__class__.__name__}

    status = int(getattr(response, "status_code", 0) or 0)
    if status == 404:
        return {**base, "error": f"仓库或分支 {info['trackRef']} 不存在"}
    if status in {403, 429}:
        return {**base, "error": "GitHub 限流(匿名每小时 60 次),过会儿再试"}
    if not 200 <= status < 300:
        return {**base, "error": f"GitHub 返回 HTTP {status}"}

    try:
        data = response.json()
    except Exception:
        return {**base, "error": "GitHub 返回的不是 JSON"}

    latest = short_sha((data or {}).get("sha"))
    if not latest:
        return {**base, "error": "GitHub 没给出 commit sha"}

    commit = (data or {}).get("commit") or {}
    return {
        **base,
        "latest": latest,
        # Never claim an update when the local build is unknown: that only means the
        # build did not inject a hash, and a false positive sends people to pull an
        # image that does not change the badge.
        "hasUpdate": bool(_SHA_RE.match(info["build"])) and latest != info["build"].lower(),
        "htmlUrl": (data or {}).get("html_url") or base["htmlUrl"],
        "publishedAt": (commit.get("committer") or {}).get("date") or (commit.get("author") or {}).get("date"),
    }
