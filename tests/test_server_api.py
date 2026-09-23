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
