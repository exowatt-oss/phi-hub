"""The MCP surface, driven over real Streamable HTTP.

These go through the actual transport rather than calling the tool
functions directly, because the things most likely to break are the wiring
(is the sub-app's lifespan running?) and the identity plumbing (do
``ctx.headers`` carry what ``deps`` expects?) — neither of which a direct
call would exercise.

The scaffold-level regressions (factory-not-singleton, nested lifespan)
are pinned in ``test_mcp_scaffold.py``; this module is about the tools.
"""

from __future__ import annotations

import base64
import json

import pytest
from fastapi.testclient import TestClient

from tests.conftest import b64

_JSON_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}
# The identity the org's mcp-edge Worker stamps after Cloudflare Access
# sign-in. Trusted here because conftest sets
# example_AUTH_TRUST_MCP_WORKER_HEADERS=true (ops sets it in prod).
_WORKER_IDENTITY = {
    "X-MCP-User-Email": "dev@example.com",
    "X-MCP-User-Name": "Dev User",
    "X-MCP-User-Id": "dev-sub",
}


class McpSession:
    """A minimal MCP client: handshake once, then call tools."""

    def __init__(self, client: TestClient, identity: dict[str, str]) -> None:
        self._client = client
        self._identity = identity
        resp = client.post(
            "/mcp/",
            headers={**_JSON_HEADERS, **identity},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "pytest", "version": "0"},
                },
            },
        )
        assert resp.status_code == 200, resp.text
        self._session_id = resp.headers["mcp-session-id"]
        client.post(
            "/mcp/",
            headers={**_JSON_HEADERS, **identity, "mcp-session-id": self._session_id},
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        )
        self._next_id = 2

    def _rpc(self, method: str, params: dict, extra_headers: dict | None = None) -> dict:
        self._next_id += 1
        resp = self._client.post(
            "/mcp/",
            headers={
                **_JSON_HEADERS,
                **self._identity,
                **(extra_headers or {}),
                "mcp-session-id": self._session_id,
            },
            json={"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": params},
        )
        assert resp.status_code == 200, resp.text
        for line in resp.text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[len("data:") :].strip())
        raise AssertionError(f"no SSE data event in response: {resp.text!r}")

    def list_tools(self) -> list[dict]:
        return self._rpc("tools/list", {})["result"]["tools"]

    def call(self, name: str, arguments: dict | None = None, **kwargs) -> dict:
        """Call a tool; returns the parsed JSON payload. Raises on tool error."""
        envelope = self._rpc("tools/call", {"name": name, "arguments": arguments or {}}, **kwargs)
        result = envelope["result"]
        assert not result.get("isError"), result
        return json.loads(result["content"][0]["text"])

    def call_expecting_error(self, name: str, arguments: dict | None = None, **kwargs) -> str:
        envelope = self._rpc("tools/call", {"name": name, "arguments": arguments or {}}, **kwargs)
        result = envelope["result"]
        assert result.get("isError"), result
        return result["content"][0]["text"]


@pytest.fixture()
def mcp(client: TestClient) -> McpSession:
    """An MCP session authenticated as ``dev@example.com`` via Worker headers."""
    return McpSession(client, _WORKER_IDENTITY)


def test_all_contract_tools_are_exposed_and_described(mcp: McpSession) -> None:
    tools = {t["name"]: t for t in mcp.list_tools()}
    assert set(tools) == {
        "hub_whoami",
        "hub_search",
        "hub_get_bundle",
        "hub_get_bundle_files",
        "hub_publish_bundle",
        "hub_record_import",
        # Marketplace (v2).
        "hub_check_updates",
    }
    # Descriptions come from the docstrings and are what the calling model
    # reads — an empty one is a real (silent) regression.
    for name, tool in tools.items():
        assert tool.get("description"), f"{name} has no description"


def test_tool_descriptions_teach_track_and_update_semantics(mcp: McpSession) -> None:
    """The docstrings are the only place a calling model learns that a
    pinned bundle must not be auto-updated. Losing that wording is a silent
    behavioural regression in every agent on the other end of this server."""
    tools = {t["name"]: t["description"] for t in mcp.list_tools()}
    assert "org_only" in tools["hub_search"]
    updates = tools["hub_check_updates"].lower()
    assert "pinned" in updates
    assert "latest" in updates
    assert "latest" in tools["hub_get_bundle_files"].lower()


def test_whoami_autocreates_the_profile(mcp: McpSession) -> None:
    body = mcp.call("hub_whoami")
    assert body["signed_in"] is True
    assert body["handle"] == "dev"
    assert body["email"] == "dev@example.com"
    assert body["bundles_published"] == 0


def test_tools_refuse_an_unidentified_caller(client: TestClient) -> None:
    """No Worker header, no token — every tool must say so rather than
    silently acting as nobody."""
    session = McpSession(client, {})
    message = session.call_expecting_error("hub_whoami")
    assert "not signed in" in message.lower()


def test_bearer_phk_token_authenticates_a_tool_call(
    client: TestClient, auth_client: TestClient
) -> None:
    """A phi agent presents its app token instead of Worker headers."""
    token = auth_client.post("/api/tokens", json={"name": "agent"}).json()["token"]
    session = McpSession(client, {"Authorization": f"Bearer {token}"})
    assert session.call("hub_whoami")["handle"] == "dev"


def test_publish_search_get_and_install_round_trip(mcp: McpSession) -> None:
    """The full agent journey: publish, find it, read it, install it, report it."""
    published = mcp.call(
        "hub_publish_bundle",
        {
            "slug": "agent-kit",
            "title": "Agent kit",
            "description": "Prompts I reach for daily",
            "kind": "prompt",
            "tags": ["prompts"],
            "changelog": "first",
            "files": [{"path": "prompts/review.md", "content_b64": b64("Review this diff.")}],
        },
    )
    assert published["ref"] == "dev/agent-kit"
    assert published["created"] is True
    assert published["version"] == 1
    assert published["share_with"] == "/hub import dev/agent-kit"

    found = mcp.call("hub_search", {"query": "agent"})
    assert [b["ref"] for b in found["bundles"]] == ["dev/agent-kit"]

    detail = mcp.call("hub_get_bundle", {"ref": "dev/agent-kit"})
    assert detail["bundle"]["ref"] == "dev/agent-kit"

    # The contract's headline requirement for this tool: instructions a
    # non-phi agent can actually follow.
    instructions = detail["import_instructions"]
    assert instructions["phi"]["command"] == "/hub import dev/agent-kit"
    assert instructions["store_path"] == "~/.pi/agent/hub/bundles/dev/agent-kit/v1"
    assert instructions["settings_path"] == "~/.pi/agent/settings.json"
    steps = " ".join(instructions["any_agent"]["steps"])
    assert "hub_get_bundle_files" in steps
    assert "packages" in steps
    assert "hub_record_import" in steps
    assert any("Never copy" in n for n in instructions["any_agent"]["never"])

    files = mcp.call("hub_get_bundle_files", {"ref": "dev/agent-kit"})
    assert files["version"] == 1
    assert files["store_path"] == "~/.pi/agent/hub/bundles/dev/agent-kit/v1"
    assert base64.b64decode(files["files"][0]["content_b64"]) == b"Review this diff."

    recorded = mcp.call(
        "hub_record_import", {"ref": "dev/agent-kit", "action": "imported", "version": 1}
    )
    assert recorded["install_count"] == 1


def test_republishing_a_slug_adds_a_version(mcp: McpSession) -> None:
    args = {
        "slug": "agent-kit",
        "title": "Agent kit",
        "description": "d",
        "kind": "prompt",
        "files": [{"path": "prompts/a.md", "content_b64": b64("one")}],
    }
    assert mcp.call("hub_publish_bundle", args)["created"] is True
    second = mcp.call("hub_publish_bundle", {**args, "changelog": "second"})
    assert second["created"] is False
    assert second["version"] == 2


def test_publish_enforces_the_same_validation_as_rest(mcp: McpSession) -> None:
    """One implementation, two surfaces — the guard can't drift."""
    base = {"slug": "guarded", "title": "T", "description": "d", "kind": "mixed"}

    traversal = mcp.call_expecting_error(
        "hub_publish_bundle",
        {**base, "files": [{"path": "../escape.md", "content_b64": b64("x")}]},
    )
    assert "escape.md" in traversal

    secret = mcp.call_expecting_error(
        "hub_publish_bundle",
        {**base, "files": [{"path": "models.json", "content_b64": b64("{}")}]},
    )
    assert "models.json" in secret

    bad_slug = mcp.call_expecting_error(
        "hub_publish_bundle",
        {**base, "slug": "Bad Slug", "files": [{"path": "a.md", "content_b64": b64("x")}]},
    )
    assert "slug" in bad_slug.lower()

    bad_kind = mcp.call_expecting_error(
        "hub_publish_bundle",
        {**base, "kind": "nonsense", "files": [{"path": "a.md", "content_b64": b64("x")}]},
    )
    assert "kind must be one of" in bad_kind

    # Nothing was stored by any of those attempts.
    assert mcp.call("hub_search", {})["total"] == 0


def test_bad_refs_and_actions_give_actionable_errors(mcp: McpSession) -> None:
    assert "owner/slug" in mcp.call_expecting_error("hub_get_bundle", {"ref": "not-a-ref"})
    assert "No bundle" in mcp.call_expecting_error("hub_get_bundle", {"ref": "nobody/nothing"})
    mcp.call(
        "hub_publish_bundle",
        {
            "slug": "thing",
            "title": "T",
            "description": "d",
            "kind": "mixed",
            "files": [{"path": "a.md", "content_b64": b64("x")}],
        },
    )
    assert "action must be one of" in mcp.call_expecting_error(
        "hub_record_import", {"ref": "dev/thing", "action": "yolo"}
    )


def test_unknown_version_says_what_exists(mcp: McpSession) -> None:
    """A wrong version number should tell the agent the valid range rather
    than leaving it to guess."""
    mcp.call(
        "hub_publish_bundle",
        {
            "slug": "thing",
            "title": "T",
            "description": "d",
            "kind": "mixed",
            "files": [{"path": "a.md", "content_b64": b64("x")}],
        },
    )
    message = mcp.call_expecting_error("hub_get_bundle_files", {"ref": "dev/thing", "version": "7"})
    assert "versions run 1..1" in message


def test_ref_tolerates_a_version_suffix(mcp: McpSession) -> None:
    """An agent pasting ``owner/slug@v1`` should get the bundle, not a parse
    error — the version is a separate argument."""
    mcp.call(
        "hub_publish_bundle",
        {
            "slug": "thing",
            "title": "T",
            "description": "d",
            "kind": "mixed",
            "files": [{"path": "a.md", "content_b64": b64("x")}],
        },
    )
    assert mcp.call("hub_get_bundle", {"ref": "dev/thing@v1"})["bundle"]["slug"] == "thing"


def test_search_filters_reach_the_same_query_as_rest(mcp: McpSession) -> None:
    for slug, kind in (("a-skill", "skill"), ("a-theme", "theme")):
        mcp.call(
            "hub_publish_bundle",
            {
                "slug": slug,
                "title": slug,
                "description": "d",
                "kind": kind,
                "files": [{"path": "a.md", "content_b64": b64("x")}],
            },
        )
    assert mcp.call("hub_search", {"kind": "skill"})["total"] == 1
    assert mcp.call("hub_search", {"owner": "dev"})["total"] == 2
    assert mcp.call("hub_search", {"owner": "nobody"})["total"] == 0


def test_search_org_only_narrows_to_the_curated_marketplace(
    mcp: McpSession, publisher_client: TestClient
) -> None:
    """The marketplace filter has to reach the same query REST uses, or an
    agent asking for "the official one" gets a personal bundle."""
    publisher_client.post(
        "/api/bundles", json={"slug": "org-kit", "title": "Org kit", "owner": "example"}
    )
    mcp.call(
        "hub_publish_bundle",
        {
            "slug": "personal",
            "title": "Personal",
            "description": "d",
            "kind": "mixed",
            "files": [{"path": "a.md", "content_b64": b64("x")}],
        },
    )
    assert mcp.call("hub_search", {})["total"] == 2
    curated = mcp.call("hub_search", {"org_only": True})
    assert [b["ref"] for b in curated["bundles"]] == ["example/org-kit"]


def test_check_updates_mirrors_the_rest_endpoint(mcp: McpSession, auth_client: TestClient) -> None:
    """Same three classifications an agent has to act on differently:
    update it, leave it alone, tell the user it is gone."""
    args = {
        "slug": "tracked",
        "title": "Tracked",
        "description": "d",
        "kind": "skill",
        "files": [{"path": "a.md", "content_b64": b64("one")}],
    }
    mcp.call("hub_publish_bundle", args)
    mcp.call("hub_publish_bundle", {**args, "changelog": "second cut"})
    mcp.call(
        "hub_publish_bundle",
        {**args, "slug": "steady", "title": "Steady", "changelog": ""},
    )

    body = mcp.call(
        "hub_check_updates",
        {
            "installed": [
                {"ref": "dev/tracked", "version": 1},
                {"ref": "dev/steady", "version": 1},
                {"ref": "dev/gone", "version": 2},
            ]
        },
    )
    assert [u["ref"] for u in body["updates"]] == ["dev/tracked"]
    assert body["updates"][0]["latest_version"] == 2
    assert body["updates"][0]["changelog"] == "second cut"
    assert body["missing"] == ["dev/gone"]

    # And it agrees with REST, which is the whole point of the shared query.
    over_rest = auth_client.post(
        "/api/updates/check",
        json={"installed": [{"ref": "dev/tracked", "version": 1}]},
    ).json()
    assert over_rest["updates"][0]["ref"] == "dev/tracked"


def test_check_updates_on_an_empty_hub_is_quiet(mcp: McpSession) -> None:
    body = mcp.call("hub_check_updates", {"installed": []})
    assert body["updates"] == []
    assert body["missing"] == []


def test_check_updates_rejects_a_malformed_entry_with_an_actionable_message(
    mcp: McpSession,
) -> None:
    """A missing ``ref`` is the agent's bug, not the user's — say so rather
    than silently checking nothing."""
    message = mcp.call_expecting_error("hub_check_updates", {"installed": [{"version": 1}]})
    assert "ref" in message


def test_check_updates_requires_identity(client: TestClient) -> None:
    session = McpSession(client, {})
    message = session.call_expecting_error("hub_check_updates", {"installed": []})
    assert "not signed in" in message.lower()


def test_mcp_published_bundle_is_visible_over_rest(
    client: TestClient, auth_client: TestClient
) -> None:
    """The two surfaces are one registry, not two."""
    session = McpSession(client, _WORKER_IDENTITY)
    session.call(
        "hub_publish_bundle",
        {
            "slug": "cross-surface",
            "title": "Cross surface",
            "description": "published over MCP",
            "kind": "skill",
            "files": [{"path": "skills/x/SKILL.md", "content_b64": b64("hi")}],
        },
    )
    detail = auth_client.get("/api/bundles/dev/cross-surface")
    assert detail.status_code == 200
    assert detail.json()["bundle"]["title"] == "Cross surface"


_MODELS_CATALOG = {
    "providers": {
        "example": {
            "name": "example Gateway",
            "baseUrl": "https://llm.example.org",
            "api": "openai-completions",
            "authHeader": True,
            "apiKey": "$example_API_KEY",
            "models": [{"id": "opus", "name": "opus", "contextWindow": 1000000}],
        }
    }
}


def test_mcp_publish_and_download_a_models_bundle(mcp: McpSession) -> None:
    """A ``kind: models`` bundle with a canonical, credential-free catalog
    publishes over MCP and downloads byte-for-byte."""
    published = mcp.call(
        "hub_publish_bundle",
        {
            "slug": "gateway-models",
            "title": "Gateway models",
            "description": "example gateway provider catalog",
            "kind": "models",
            "changelog": "first catalog",
            "files": [
                {
                    "path": "models/models.json",
                    "content_b64": b64(json.dumps(_MODELS_CATALOG)),
                }
            ],
        },
    )
    assert published["ref"] == "dev/gateway-models"
    assert published["version"] == 1

    detail = mcp.call("hub_get_bundle", {"ref": "dev/gateway-models"})
    assert detail["bundle"]["kind"] == "models"

    files = mcp.call("hub_get_bundle_files", {"ref": "dev/gateway-models"})
    entry = next(f for f in files["files"] if f["path"] == "models/models.json")
    assert base64.b64decode(entry["content_b64"]) == json.dumps(_MODELS_CATALOG).encode()


def test_mcp_publish_rejects_a_literal_credential_in_models_json(mcp: McpSession) -> None:
    """The secret guard is shared, so the MCP surface refuses a literal
    provider key exactly as REST does, naming the offending path."""
    leaked = json.loads(json.dumps(_MODELS_CATALOG))
    leaked["providers"]["example"]["apiKey"] = "sk-ant-api03-abcdef123456"
    message = mcp.call_expecting_error(
        "hub_publish_bundle",
        {
            "slug": "leaky-models",
            "title": "Leaky",
            "description": "has a real key",
            "kind": "models",
            "files": [{"path": "models/models.json", "content_b64": b64(json.dumps(leaked))}],
        },
    )
    assert "models/models.json" in message
    assert "credential" in message.lower()


def test_mcp_publish_rejects_a_noncanonical_models_json(mcp: McpSession) -> None:
    """A models.json anywhere but the canonical path is refused by basename."""
    message = mcp.call_expecting_error(
        "hub_publish_bundle",
        {
            "slug": "stray-models",
            "title": "Stray",
            "description": "wrong path",
            "kind": "models",
            "files": [{"path": "models.json", "content_b64": b64(json.dumps(_MODELS_CATALOG))}],
        },
    )
    assert "models.json" in message
