import json

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
