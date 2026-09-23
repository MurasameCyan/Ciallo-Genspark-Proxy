from pathlib import Path

from app import server
from app.server import PanelAuth


def make_auth(monkeypatch, tmp_path, env_pass="", file_text=None, file_name="panel_password"):
    monkeypatch.setattr(server, "ROOT_DIR", tmp_path)
    monkeypatch.setenv("PANEL_PASS", env_pass)
    monkeypatch.delenv("PANEL_PASS_FILE", raising=False)
    if file_text is not None:
        (tmp_path / file_name).write_text(file_text, encoding="utf-8")
    return PanelAuth()


def test_password_file_overrides_environment_default(monkeypatch, tmp_path):
    auth = make_auth(monkeypatch, tmp_path, env_pass="from-env", file_text="from-file")

    assert auth.password == "from-file"
    assert auth.password_source == "file"
    assert auth.login("admin", "from-file") is not None
    assert auth.login("admin", "from-env") is None


def test_environment_is_used_when_no_file_exists(monkeypatch, tmp_path):
    auth = make_auth(monkeypatch, tmp_path, env_pass="from-env")

    assert auth.password == "from-env"
    assert auth.password_source == "env"
    assert auth.login("admin", "from-env") is not None


def test_password_file_is_used_when_env_is_empty(monkeypatch, tmp_path):
    auth = make_auth(monkeypatch, tmp_path, file_text="from-file\n")

    assert auth.password == "from-file"
    assert auth.enabled is True
    assert auth.login("admin", "from-file") is not None
    assert auth.login("admin", "wrong") is None

def test_explicit_password_file_path_is_honoured(monkeypatch, tmp_path):
    elsewhere = tmp_path / "nested"
    elsewhere.mkdir()
    secret = elsewhere / "secret.txt"
    secret.write_text("explicit-pass", encoding="utf-8")
    monkeypatch.setattr(server, "ROOT_DIR", tmp_path / "app-root")
    monkeypatch.setenv("PANEL_PASS", "")
    monkeypatch.setenv("PANEL_PASS_FILE", str(secret))

    assert PanelAuth().password == "explicit-pass"


def test_missing_file_disables_login_protection(monkeypatch, tmp_path):
    auth = make_auth(monkeypatch, tmp_path)

    assert auth.password == ""
    assert auth.password_source == "none"
    assert auth.enabled is False


def test_default_file_location_is_next_to_the_app(monkeypatch, tmp_path):
    target = tmp_path / "app-root"
    target.mkdir()
    make_auth(monkeypatch, target, file_text="sidecar-pass")

    assert (target / "panel_password").is_file()
    assert PanelAuth().password == "sidecar-pass"
