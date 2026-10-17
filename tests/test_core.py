from __future__ import annotations

from gramps_mcp import core


def test_gramps_client_refreshes_auth_header(monkeypatch):
    monkeypatch.setattr(core, "GRAMPS_BACKEND_URL", "https://gramps.example.com")

    refresh_calls: list[str] = []
    initial_token = "initial-access"
    refreshed_token = "refreshed-access"
    refreshed_refresh = "refreshed-refresh"

    class FakeClient:
        def __init__(self) -> None:
            self.headers = {"Authorization": f"Bearer {initial_token}"}

    class FakeResponse:
        status_code = 200

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    def fake_post(url, json=None, timeout=None):
        assert url == "https://gramps.example.com/api/token/refresh/"
        refresh_calls.append(json["token"])
        return FakeResponse(
            {"access_token": refreshed_token, "refresh_token": refreshed_refresh}
        )

    monkeypatch.setattr(core.httpx2, "post", fake_post)

    client = FakeClient()
    next_refresh_token = core.refresh_client_headers(client, "seed-refresh")

    assert refresh_calls == ["seed-refresh"]
    assert next_refresh_token == refreshed_refresh
    assert client.headers["Authorization"] == f"Bearer {refreshed_token}"


def test_admin_emails_parsed_from_env(monkeypatch):
    monkeypatch.setenv(
        "GRAMPS_MCP_ADMIN_EMAILS", " Alice@Example.com, bob@example.com "
    )
    import importlib

    reloaded = importlib.reload(core)
    try:
        assert reloaded.ADMIN_EMAILS == {"alice@example.com", "bob@example.com"}
    finally:
        monkeypatch.delenv("GRAMPS_MCP_ADMIN_EMAILS", raising=False)
        importlib.reload(core)


def test_goql_guide_is_packaged():
    from importlib import resources

    text = (
        resources.files("gramps_mcp")
        .joinpath("goql_docs.md")
        .read_text(encoding="utf-8")
    )
    assert "where_expr" in text
    assert "MatchesQuery" in text


def test_auth_is_automatic_unless_switched_off(monkeypatch):
    monkeypatch.delenv("GRAMPS_MCP_AUTH", raising=False)
    assert not core.auth_disabled()
    for value in ("auto", " AUTO "):
        monkeypatch.setenv("GRAMPS_MCP_AUTH", value)
        assert not core.auth_disabled()
    for value in ("off", " Off "):
        monkeypatch.setenv("GRAMPS_MCP_AUTH", value)
        assert core.auth_disabled()


def test_auth_setting_rejects_other_values(monkeypatch):
    import pytest

    monkeypatch.setenv("GRAMPS_MCP_AUTH", "none")
    with pytest.raises(ValueError, match="must be auto or off"):
        core.auth_disabled()


def test_auth_off_wins_over_google_credentials_on_a_local_server(monkeypatch, caplog):
    monkeypatch.setenv("GRAMPS_MCP_AUTH", "off")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret")
    for base_url in (
        None,
        "http://localhost:8000",
        "http://127.0.0.1:9000",
        "http://[::1]:8000",
    ):
        if base_url is None:
            monkeypatch.delenv("MCP_BASE_URL", raising=False)
        else:
            monkeypatch.setenv("MCP_BASE_URL", base_url)
        with caplog.at_level("WARNING", logger="gramps_mcp"):
            assert core.build_google_auth_provider() is None
    assert "authentication is disabled" in caplog.text


def test_auth_off_is_refused_for_a_public_server(monkeypatch):
    import pytest

    monkeypatch.setenv("GRAMPS_MCP_AUTH", "off")
    monkeypatch.setenv("MCP_BASE_URL", "https://mcp.example.com")
    with pytest.raises(RuntimeError, match="only for a local server"):
        core.build_google_auth_provider()


def test_without_google_credentials_there_is_no_auth(monkeypatch):
    monkeypatch.delenv("GRAMPS_MCP_AUTH", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    assert core.build_google_auth_provider() is None
