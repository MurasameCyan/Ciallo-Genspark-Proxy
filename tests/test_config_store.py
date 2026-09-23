import json

from app import config_store as app_config_store
from app.config_store import ConfigStore


def make_store(tmp_path, values=None):
    path = tmp_path / "config.json"
    if values is not None:
        path.write_text(json.dumps(values), encoding="utf-8")
    return ConfigStore(path)


def test_captcha_key_is_masked_and_preserved(tmp_path):
    store = make_store(tmp_path, {"twocaptcha_key": "secret-key", "twocaptcha_proxy": "http://proxy:8080"})

    public = store.public()
    assert public["twocaptcha_key"] == ""
    assert public["twocaptcha_key_set"] is True
    assert public["twocaptcha_proxy"] == "http://proxy:8080"

    store.save({"twocaptcha_key": "", "twocaptcha_proxy": "http://proxy:9090"})
    assert store.load()["twocaptcha_key"] == "secret-key"
    assert store.load()["twocaptcha_proxy"] == "http://proxy:9090"


def test_auto_solve_defaults_on_and_can_be_disabled(tmp_path):
    store = make_store(tmp_path)
    assert store.load()["register_auto_solve"] is True

    store.save({"register_auto_solve": False})
    assert store.load()["register_auto_solve"] is False

    store.save({"register_auto_solve": "yes"})
    assert store.load()["register_auto_solve"] is True


def test_password_is_recorded_from_driver_log(tmp_path):
    store = make_store(tmp_path)
    store.save({"register_password": "  Gs!secret9z  "})
    assert store.load()["register_password"] == "Gs!secret9z"

    store.save({"register_password": ""})
    assert store.load()["register_password"] == "Gs!secret9z"


def test_save_does_not_freeze_environment_defaults(tmp_path, monkeypatch):
    monkeypatch.setitem(app_config_store.DEFAULT_CONFIG, "accounts_file", "/data/accounts.json")
    monkeypatch.setitem(app_config_store.DEFAULT_CONFIG, "mail_api_base", "https://mail.example")
    store = make_store(tmp_path)

    store.save({"mail_domain": "example.com"})

    saved = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert saved == {"mail_domain": "example.com"}
    assert store.load()["accounts_file"] == "/data/accounts.json"
    assert store.load()["mail_api_base"] == "https://mail.example"


def test_saved_values_still_win_over_environment_defaults(tmp_path, monkeypatch):
    monkeypatch.setitem(app_config_store.DEFAULT_CONFIG, "mail_poll_interval", 9.0)
    store = make_store(tmp_path)

    store.save({"mail_poll_interval": 4})
    monkeypatch.setitem(app_config_store.DEFAULT_CONFIG, "mail_poll_interval", 30.0)

    assert make_store(tmp_path).load()["mail_poll_interval"] == 4