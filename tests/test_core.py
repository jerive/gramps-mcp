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
    monkeypatch.setenv("GRAMPS_MCP_ADMIN_EMAILS", " Alice@Example.com, bob@example.com ")
    import importlib

    reloaded = importlib.reload(core)
    try:
        assert reloaded.ADMIN_EMAILS == {"alice@example.com", "bob@example.com"}
    finally:
        monkeypatch.delenv("GRAMPS_MCP_ADMIN_EMAILS", raising=False)
        importlib.reload(core)
