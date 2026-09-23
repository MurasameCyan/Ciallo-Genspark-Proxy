import json
from pathlib import Path

from app.registration import RegistrationService, parse_driver_password


class StubConfig:
    def __init__(self, values=None):
        self.values = dict(values or {})

    def load(self):
        return dict(self.values)

    def save(self, patch):
        self.values.update(patch)
        return dict(self.values)


class StubAccounts:
    def __init__(self):
        self.written: list[dict] = []

    def next_seq(self):
        return len(self.written) + 1

    def upsert(self, record):
        self.written.append(dict(record))
        return dict(record)


class StubProcess:
    def __init__(self):
        self.returncode = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = 0

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.returncode = -9


def make_service(tmp_path, monkeypatch, config=None):
    service = RegistrationService(
        StubConfig(config or {}),
        StubAccounts(),
        lambda *_args: None,
        root_dir=tmp_path,
        data_dir=tmp_path / "data",
    )
    service._sleep = lambda _seconds: None
    monkeypatch.setattr(service, "_run", lambda _job: None)
    return service


def start_job(service):
    service.start(seq=7, email="bot@example.com", proxy="http://127.0.0.1:9")
    job = service._job
    job.process = StubProcess()
    return job


def record_commands(service, sink):
    real = service._write_command

    def write(job, line):
        sink.append(line)
        real(job, line)

    service._write_command = write


def test_command_maps_captcha_and_inspection_commands(tmp_path, monkeypatch):
    service = make_service(tmp_path, monkeypatch)
    job = start_job(service)
    written: list[str] = []
    record_commands(service, written)

    for command in ("autocap", "capimg", "caprefresh", "state", "pages", "extract"):
        service.command(command)
    service.command("captcha", "ab12")
    service.command("click", "Verify code")
    service.command("type", "#emailVerificationCode|123456")
    service.command("goto", "https://www.genspark.ai/agents")
    service.command("fill", "#newPassword\tGs!abc9z")

    assert written == [
        "autocap",
        "capimg",
        "caprefresh",
        "state",
        "pages",
        "extract",
        "captcha=ab12",
        "click=Verify code",
        "type=#emailVerificationCode|123456",
        "goto=https://www.genspark.ai/agents",
        "fill\t#newPassword\tGs!abc9z",
    ]
    assert job.captcha_requested is True
    assert service.command("state")["command"] == "state"


def test_code_command_normalizes_digits(tmp_path, monkeypatch):
    service = make_service(tmp_path, monkeypatch)
    job = start_job(service)

    service.command("code", "12-34 56")
    assert job.code == "123456"


def test_parse_driver_password_reads_log_line():
    assert parse_driver_password("[00:12:34] [password] Gs!abc1234z") == "Gs!abc1234z"
    assert parse_driver_password("no password here") == ""


def test_command_rejects_unknown_and_value_less_commands(tmp_path, monkeypatch):
    service = make_service(tmp_path, monkeypatch)
    start_job(service)

    for command, value in (("autocap_typo", ""), ("captcha", ""), ("type", ""), ("click", "")):
        try:
            service.command(command, value)
        except ValueError:
            continue
        raise AssertionError(f"expected rejection for {command!r}")


def test_export_uses_gs_export_cli_and_records_identity(tmp_path, monkeypatch):
    (tmp_path / "gs_export.py").write_text("# export stub\n", encoding="utf-8")
    service = make_service(tmp_path, monkeypatch, {"mail_timeout": 30})
    job = start_job(service)
    job.state = "exporting_cookie"
    job.password = "Gs!secret9z"

    calls: list[dict] = []

    def fake_run(argv, **kwargs):
        calls.append({"argv": list(argv), "env": kwargs.get("env") or {}})
        out = Path(argv[argv.index("--out") + 1])
        out.write_text(
            json.dumps({"cogen_id": "cogen-42", "email": "bot@example.com", "cookies": [{"name": "session_id", "value": "v"}]}),
            encoding="utf-8",
        )
        return type("Result", (), {"returncode": 0, "stdout": "OK exported", "stderr": ""})()

    monkeypatch.setattr("app.registration.subprocess.run", fake_run)
    service._export_cookie(job)

    argv = calls[0]["argv"]
    assert argv[1].endswith("gs_export.py")
    assert "--profile" in argv and str(job.profile_dir) in argv
    assert "--out" in argv and str(job.cookie_file) in argv
    record = service.accounts.written[-1]
    assert record["cogen_id"] == "cogen-42"
    assert record["password"] == "Gs!secret9z"
    assert record["status"] == "active"
    assert job.state == "succeeded"


def test_drive_flow_auto_solves_captcha_then_creates_account(tmp_path, monkeypatch):
    (tmp_path / "gs_export.py").write_text("# export stub\n", encoding="utf-8")
    service = make_service(
        tmp_path,
        monkeypatch,
        {"register_auto_solve": True, "twocaptcha_key": "key-1", "mail_timeout": 30},
    )
    job = start_job(service)
    job.mailbox_token = "mailbox-jwt"
    job.password = "Gs!auto9z"
    job.log_text = "[00:00:01] [fill:#email] ok try1\n[00:00:40] [autocap] PASSED with answer='ab12'\n[00:02:00] [state:after_create] url=https://www.genspark.ai/agents"

    class FakeMail:
        def __init__(self, *_args, **_kwargs):
            pass

        def wait_for_code(self, *_args, **_kwargs):
            return "654321"

    def fake_export(argv, **_kwargs):
        Path(argv[argv.index("--out") + 1]).write_text(
            json.dumps({"cogen_id": "cogen-77", "cookies": [{"name": "session_id", "value": "v"}]}),
            encoding="utf-8",
        )
        return type("Result", (), {"returncode": 0, "stdout": "OK", "stderr": ""})()

    monkeypatch.setattr("app.registration.CloudflareMailClient", FakeMail)
    monkeypatch.setattr("app.registration.subprocess.run", fake_export)

    written: list[str] = []
    record_commands(service, written)
    service._drive_flow(job, timeout=5)

    assert "autocap" in written
    assert "type=#emailVerificationCode|654321" in written
    assert written[-3:] == ["password", "create", "quit"]
    assert job.state == "succeeded"
    assert service.accounts.written[-1]["cogen_id"] == "cogen-77"
