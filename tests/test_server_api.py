import asyncio

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

