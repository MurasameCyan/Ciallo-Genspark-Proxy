import asyncio
from urllib.parse import urlencode

import pytest
from fastapi import HTTPException

from app import server
from app.registration import RegistrationJob


class OpenAuth:
    enabled = False

    def valid(self, _request):
        return True


class FakeRequest:
    def __init__(self, body=None):
        self._body = body or {}
        self.cookies: dict[str, str] = {}
        self.headers: dict[str, str] = {}

    async def json(self):
        return self._body


@pytest.fixture(autouse=True)
def open_panel(monkeypatch):
    monkeypatch.setattr(server, "auth", OpenAuth())


@pytest.fixture(autouse=True)
def clean_job():
    server.registration._job = None
    yield
    server.registration._job = None


def run(coro):
    return asyncio.run(coro)


def test_command_without_running_job_reports_conflict():
    with pytest.raises(HTTPException) as excinfo:
        run(server.register_command(FakeRequest({"command": "autocap"})))

    assert excinfo.value.status_code == 409
    assert "没有运行中的注册任务" in excinfo.value.detail


def test_unknown_command_reports_bad_request():
    server.registration._job = RegistrationJob(job_id="job1", seq=1, state="waiting_for_captcha")

    with pytest.raises(HTTPException) as excinfo:
        run(server.register_command(FakeRequest({"command": "autocap_typo"})))

    assert excinfo.value.status_code == 400
    assert "不支持" in excinfo.value.detail


def test_start_while_running_reports_conflict():
    server.registration._job = RegistrationJob(job_id="job1", seq=1, state="waiting_for_captcha")

    with pytest.raises(HTTPException) as excinfo:
        run(server.register_start(FakeRequest({"seq": 2})))

    assert excinfo.value.status_code == 409
    assert "已有注册任务" in excinfo.value.detail


def test_start_rejects_non_integer_seq():
    with pytest.raises(HTTPException) as excinfo:
        run(server.register_start(FakeRequest({"seq": "abc"})))

    assert excinfo.value.status_code == 400


class FormRequest(FakeRequest):
    """Encodes the fields the way a browser submits the form, so the test exercises
    the same body parsing the container runs (no stubbed form() to hide a gap)."""

    def __init__(self, user, password):
        super().__init__({})
        self.headers = {"content-type": "application/x-www-form-urlencoded"}
        self._body = urlencode({"user": user, "pass": password}).encode()

    async def body(self):
        return self._body

@pytest.fixture
def locked_auth(monkeypatch):
    class FixedAuth:
        user = "admin"
        password = "correct-pass"
        password_source = "env"
        enabled = True

        def valid(self, _request):
            return False

        def login(self, user, password):
            if user != self.user or password != self.password:
                return None
            return "token-123"

    monkeypatch.setattr(server, "auth", FixedAuth())


def test_form_login_redirects_to_dashboard_and_sets_cookie(locked_auth):
    response = run(server.login(FormRequest("admin", "correct-pass")))

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert "gs_session=token-123" in response.headers["set-cookie"]


def test_form_login_failure_returns_to_login_page(locked_auth):
    response = run(server.login(FormRequest("admin", "nope")))

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login.html?error=1")
    assert "gs_session" not in response.headers.get("set-cookie", "")


def test_json_login_still_returns_json(locked_auth):
    response = run(server.login(FakeRequest({"user": "admin", "pass": "correct-pass"})))

    assert response.status_code == 200
    assert "gs_session=token-123" in response.headers["set-cookie"]



def test_status_carries_build_identity(monkeypatch):
    monkeypatch.setattr(server, "build_info", lambda: {"build": "abc1234", "buildUrl": "u", "repoUrl": "r", "trackRef": "main"})

    payload = server.api_status(FakeRequest())

    assert payload["build"] == "abc1234"
    assert payload["trackRef"] == "main"


def test_check_update_route_returns_result_and_logs(monkeypatch):
    emitted: list[tuple[str, str]] = []
    monkeypatch.setattr(server.logs, "emit", lambda level, message: emitted.append((level, message)))
    monkeypatch.setattr(
        server,
        "check_update",
        lambda: {"current": "old1111", "latest": "new2222", "hasUpdate": True, "error": None},
    )

    result = server.api_check_update(FakeRequest())

    assert result["hasUpdate"] is True
    assert emitted and emitted[0][0] == "ok"
    assert "new2222" in emitted[0][1]


def test_check_update_route_logs_failure(monkeypatch):
    emitted: list[tuple[str, str]] = []
    monkeypatch.setattr(server.logs, "emit", lambda level, message: emitted.append((level, message)))
    monkeypatch.setattr(
        server,
        "check_update",
        lambda: {"current": "old1111", "latest": None, "hasUpdate": False, "error": "GitHub 限流(匿名每小时 60 次),过会儿再试"},
    )

    result = server.api_check_update(FakeRequest())

    assert result["error"].startswith("GitHub 限流")
    assert emitted[0][0] == "warn"

