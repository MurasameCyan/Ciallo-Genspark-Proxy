import pytest

from app import build


class FakeResponse:
    def __init__(self, status_code=200, payload=None, body=None):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self._payload = payload
        self._body = body

    def json(self):
        if self._body is not None:
            raise ValueError("not json")
        return self._payload


@pytest.fixture(autouse=True)
def clear_cache():
    build._CACHED_BUILD = None
    yield
    build._CACHED_BUILD = None


def test_short_sha_normalizes_hashes_and_placeholders():
    assert build.short_sha("FC45E5FC9C0A") == "fc45e5f"
    assert build.short_sha("v1.2.3") == "v1.2.3"
    assert build.short_sha("unknown") == ""
    assert build.short_sha("None") == ""
    assert build.short_sha("") == ""
    assert len(build.short_sha("a" * 40)) == 7


def test_build_id_prefers_environment(monkeypatch):
    monkeypatch.setenv("GIT_COMMIT", "1234567890abcdef")
    assert build.build_id() == "1234567"


def test_build_info_links_commit_or_branch(monkeypatch):
    monkeypatch.setenv("GIT_COMMIT", "1234567890abcdef")
    info = build.build_info()
    assert info["build"] == "1234567"
    assert info["buildUrl"].endswith("/commit/1234567")
    monkeypatch.setenv("GIT_COMMIT", "not-a-sha")
    build._CACHED_BUILD = None
    assert build.build_info()["buildUrl"].endswith("/commits/main")


def test_repo_and_ref_can_be_overridden(monkeypatch):
    monkeypatch.setenv("GITHUB_REPO", "https://github.com/fork/own-proxy/")
    monkeypatch.setenv("GITHUB_TRACK_REF", "beta")
    info = build.build_info()
    assert info["repoUrl"] == "https://github.com/fork/own-proxy"
    assert info["trackRef"] == "beta"


def fetcher_for(response):
    calls: list[dict] = []

    def fetcher(url, **kwargs):
        calls.append({"url": url, **kwargs})
        if isinstance(response, Exception):
            raise response
        return response

    fetcher.calls = calls
    return fetcher


def test_check_update_reports_newer_commit(monkeypatch):
    monkeypatch.setenv("GIT_COMMIT", "1111111111111")
    payload = {
        "sha": "2222222222222222222222222222222222222222",
        "html_url": "https://github.com/o/r/commit/222",
        "commit": {"committer": {"date": "2026-09-24T00:00:00Z"}},
    }
    fetcher = fetcher_for(FakeResponse(payload=payload))

    result = build.check_update(fetcher)

    assert result["current"] == "1111111"
    assert result["latest"] == "2222222"
    assert result["hasUpdate"] is True
    assert result["error"] is None
    assert result["publishedAt"] == "2026-09-24T00:00:00Z"
    assert fetcher.calls[0]["url"].endswith("/commits/main")


def test_check_update_is_quiet_when_current(monkeypatch):
    monkeypatch.setenv("GIT_COMMIT", "2222222222222")
    result = build.check_update(fetcher_for(FakeResponse(payload={"sha": "2222222222222222"})))
    assert result["hasUpdate"] is False


def test_check_update_stays_quiet_when_build_unknown(monkeypatch):
    monkeypatch.setenv("GIT_COMMIT", "")
    monkeypatch.setattr(build, "_from_git", lambda: "")
    result = build.check_update(fetcher_for(FakeResponse(payload={"sha": "2222222222222222"})))
    assert result["hasUpdate"] is False
    assert result["latest"] == "2222222"


@pytest.mark.parametrize(
    ("response", "fragment"),
    [
        (FakeResponse(status_code=404), "不存在"),
        (FakeResponse(status_code=403), "限流"),
        (FakeResponse(status_code=429), "限流"),
        (FakeResponse(status_code=500), "HTTP 500"),
        (FakeResponse(body="<html>"), "JSON"),
        (FakeResponse(payload={"sha": ""}), "commit sha"),
    ],
)
def test_check_update_reports_failures_without_raising(monkeypatch, response, fragment):
    monkeypatch.setenv("GIT_COMMIT", "1111111111111")
    result = build.check_update(fetcher_for(response))
    assert result["hasUpdate"] is False
    assert fragment in result["error"]


def test_check_update_survives_network_failure(monkeypatch):
    monkeypatch.setenv("GIT_COMMIT", "1111111111111")
    result = build.check_update(fetcher_for(TimeoutError("timed out")))
    assert result["hasUpdate"] is False
    assert "timed out" in result["error"]
