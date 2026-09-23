#!/bin/sh
# Verifies a running Ciallo Genspark Proxy instance end to end: health, protected
# API, both login paths (JSON and the native form post the panel falls back to when
# JavaScript never runs), and the authenticated dashboard markup.
#
#   BASE_URL=http://127.0.0.1:8899 PANEL_USER=admin PANEL_PASS=secret ./scripts/smoke.sh [expected_sha]
#
# Uses python3 rather than curl: the runtime image ships python but not curl, so the
# same script runs inside the container and on a host.
set -eu

BASE_URL="${BASE_URL:-http://127.0.0.1:8899}"
PANEL_USER="${PANEL_USER:-admin}"
PANEL_PASS="${PANEL_PASS:?PANEL_PASS must be set}"
EXPECTED_SHA="${1:-}"

PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || PY=python
command -v "$PY" >/dev/null 2>&1 || { echo "FAIL: no python3/python on PATH" >&2; exit 1; }

BASE_URL="$BASE_URL" PANEL_USER="$PANEL_USER" PANEL_PASS="$PANEL_PASS" EXPECTED_SHA="$EXPECTED_SHA" "$PY" - <<'PY'
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import http.cookiejar

BASE = os.environ["BASE_URL"].rstrip("/")
USER = os.environ["PANEL_USER"]
PASSWORD = os.environ["PANEL_PASS"]
EXPECTED = os.environ.get("EXPECTED_SHA") or ""
FAILURES = []


def check(label, ok, detail=""):
    mark = "ok  " if ok else "FAIL"
    print(f"{mark} {label}" + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(label)
        if detail:
            print(f"     {detail}")


def open_json(path):
    with urllib.request.urlopen(f"{BASE}{path}", timeout=10) as response:
        return json.load(response)


def opener_with_cookie():
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar)), jar


print("== health ==")
try:
    health = open_json("/health")
    check("health reports ok", health.get("ok") is True, json.dumps(health))
except Exception as exc:
    check("health request", False, str(exc))
    health = {}

print("== unauthenticated API is protected ==")
try:
    urllib.request.urlopen(f"{BASE}/api/status", timeout=10)
    check("unauthenticated /api/status is rejected", False, "returned 200")
except urllib.error.HTTPError as exc:
    check("unauthenticated /api/status is rejected", exc.code == 401, f"HTTP {exc.code}")

print("== JSON login ==")
opener, jar = opener_with_cookie()
body = json.dumps({"user": USER, "pass": PASSWORD}).encode()
try:
    with opener.open(urllib.request.Request(f"{BASE}/api/login", data=body, headers={"Content-Type": "application/json"}), timeout=10) as response:
        check("JSON login returns 200", response.status == 200, f"HTTP {response.status}")
except urllib.error.HTTPError as exc:
    check("JSON login returns 200", False, f"HTTP {exc.code} {exc.read()[:120]!r}")
names = {cookie.name for cookie in jar}
check("JSON login sets gs_session", "gs_session" in names, str(sorted(names)))

print("== status reports the injected build ==")
try:
    with opener.open(f"{BASE}/api/status", timeout=10) as response:
        status = json.load(response)
    for field in ("build", "buildUrl", "repoUrl", "trackRef"):
        check(f"status carries {field}", bool(status.get(field)), json.dumps(status, ensure_ascii=False))
    if EXPECTED:
        check(f"status build is {EXPECTED}", status.get("build") == EXPECTED, str(status.get("build")))
except Exception as exc:
    check("status request", False, str(exc))

print("== dashboard markup ==")
try:
    with opener.open(f"{BASE}/", timeout=10) as response:
        page = response.read().decode("utf-8", "replace")
    for token in ("registration-form", "build-id", "update-button", "repo-link", "captcha-mode-pill"):
        check(f"markup has #{token}", token in page)
except Exception as exc:
    check("dashboard request", False, str(exc))

print("== native form login (no JavaScript) ==")
form_opener, form_jar = opener_with_cookie()
form = urllib.parse.urlencode({"user": USER, "pass": PASSWORD}).encode()
try:
    with form_opener.open(
        urllib.request.Request(f"{BASE}/api/login", data=form, headers={"Content-Type": "application/x-www-form-urlencoded"}),
        timeout=10,
    ) as response:
        check("form login ends on the dashboard", response.url.rstrip("/") == BASE, response.url)
        check("form login returns 200", response.status == 200, f"HTTP {response.status}")
except urllib.error.HTTPError as exc:
    check("form login ends on the dashboard", False, f"HTTP {exc.code} {exc.read()[:120]!r}")
form_names = {cookie.name for cookie in form_jar}
check("form login sets gs_session", "gs_session" in form_names, str(sorted(form_names)))
try:
    with form_opener.open(f"{BASE}/", timeout=10) as response:
        check("form-login session loads the dashboard", "registration-form" in response.read().decode("utf-8", "replace"))
except Exception as exc:
    check("form-login session loads the dashboard", False, str(exc))

print("== rejected credential bounces back to the form ==")
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


bad = urllib.parse.urlencode({"user": USER, "pass": "definitely-wrong"}).encode()
try:
    urllib.request.build_opener(NoRedirect()).open(
        urllib.request.Request(f"{BASE}/api/login", data=bad, headers={"Content-Type": "application/x-www-form-urlencoded"}),
        timeout=10,
    )
    check("wrong password redirects to the form", False, "returned 200")
except urllib.error.HTTPError as exc:
    location = exc.headers.get("location") or ""
    check("wrong password redirects to the form", exc.code == 303 and location.startswith("/login.html"), f"HTTP {exc.code} location={location}")

if FAILURES:
    print(f"\nsmoke: {len(FAILURES)} check(s) failed")
    sys.exit(1)
print("\nsmoke: all checks passed")
PY
