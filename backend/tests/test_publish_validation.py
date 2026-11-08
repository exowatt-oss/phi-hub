"""Publish validation — path safety, caps, and every secret-guard pattern.

``README.md`` → "Server-side validation on publish". Two properties
matter beyond "it returns 422":

1. The response NAMES the offending path, so a publisher (human or agent)
   can fix the right file instead of bisecting their bundle.
2. A rejected payload is never stored — not even partially. Several tests
   publish a good file alongside a bad one and assert the version count
   didn't move.
"""

from __future__ import annotations

import base64
import json

import pytest
from fastapi.testclient import TestClient

from app import hub
from tests.conftest import b64

_OK = {"path": "skills/ok/SKILL.md", "content_b64": b64("# fine\n")}


def _publish(client: TestClient, *files: dict) -> object:
    return client.post("/api/bundles/dev/routing-setup/versions", json={"files": list(files)})


def _reject(client: TestClient, bad: dict) -> dict:
    """Publish ``bad`` alongside a good file; assert 422 + nothing stored."""
    before = client.get("/api/bundles/dev/routing-setup").json()["bundle"]["latest_version"]
    resp = _publish(client, _OK, bad)
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    after = client.get("/api/bundles/dev/routing-setup").json()["bundle"]["latest_version"]
    assert after == before, "a rejected publish must not create a version"
    return detail


# ------------------------------------------------------------- path safety


@pytest.mark.parametrize(
    "path",
    [
        "../evil.md",
        "skills/../../evil.md",
        "..",
        "/etc/passwd",
        "//etc/passwd",
        "skills//double.md",
        "./relative.md",
        "a/./b.md",
        "C:/windows/system32",
        "skills\\windows\\SKILL.md",
        "..\\..\\evil.md",
        ".git/config",
        "skills/.git/HEAD",
    ],
)
def test_unsafe_paths_are_refused_by_path(
    published_bundle: dict, auth_client: TestClient, path: str
) -> None:
    detail = _reject(auth_client, {"path": path, "content_b64": b64("x")})
    assert detail["error"] in {"invalid_path", "duplicate_path"}
    assert detail["path"] == path


def test_safe_nested_paths_are_accepted(published_bundle: dict, auth_client: TestClient) -> None:
    resp = _publish(
        auth_client,
        {"path": "skills/deeply/nested/thing/SKILL.md", "content_b64": b64("ok")},
        {"path": "extensions/hub.ts", "content_b64": b64("export {}")},
        {"path": "prompts/review.md", "content_b64": b64("review this")},
        {"path": "instructions/AGENTS.md", "content_b64": b64("be terse")},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["version"]["version"] == 2


def test_duplicate_paths_are_refused(published_bundle: dict, auth_client: TestClient) -> None:
    resp = _publish(auth_client, _OK, dict(_OK))
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "duplicate_path"


# -------------------------------------------------------------------- caps


def test_file_size_cap(published_bundle: dict, auth_client: TestClient) -> None:
    oversized = base64.b64encode(b"x" * (hub.MAX_FILE_BYTES + 1)).decode("ascii")
    detail = _reject(auth_client, {"path": "big.md", "content_b64": oversized})
    assert detail["error"] == "file_too_large"
    assert detail["path"] == "big.md"


def test_file_exactly_at_the_cap_is_accepted(
    published_bundle: dict, auth_client: TestClient
) -> None:
    """Off-by-one guard: the cap is inclusive."""
    at_cap = base64.b64encode(b"x" * hub.MAX_FILE_BYTES).decode("ascii")
    resp = _publish(auth_client, {"path": "atcap.md", "content_b64": at_cap})
    assert resp.status_code == 201, resp.text


def test_version_size_cap(published_bundle: dict, auth_client: TestClient) -> None:
    """Eleven 512 KB files clear the per-file cap but bust the 5 MB version cap."""
    chunk = base64.b64encode(b"y" * hub.MAX_FILE_BYTES).decode("ascii")
    files = [{"path": f"f{i}.md", "content_b64": chunk} for i in range(11)]
    resp = _publish(auth_client, *files)
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "version_too_large"


def test_file_count_cap(published_bundle: dict, auth_client: TestClient) -> None:
    files = [{"path": f"f{i}.md", "content_b64": b64("x")} for i in range(hub.MAX_FILES + 1)]
    resp = _publish(auth_client, *files)
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "too_many_files"


def test_empty_version_is_refused(published_bundle: dict, auth_client: TestClient) -> None:
    resp = auth_client.post("/api/bundles/dev/routing-setup/versions", json={"files": []})
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "empty_version"


def test_bad_base64_is_refused(published_bundle: dict, auth_client: TestClient) -> None:
    detail = _reject(auth_client, {"path": "broken.md", "content_b64": "not!base64!"})
    assert detail["error"] == "invalid_base64"


# ------------------------------------------------------------ secret guard


@pytest.mark.parametrize(
    "path",
    [
        "models.json",
        "settings/models.json",
        "auth.json",
        "nested/deep/auth.json",
        "credentials",
        "credentials.json",
        "credentials.yaml",
        ".env",
        ".env.local",
        "config/.env.production",
        "hub.json",
        "mcp.json",
        "settings/mcp.json",
    ],
)
def test_secret_filenames_are_refused(
    published_bundle: dict, auth_client: TestClient, path: str
) -> None:
    """Every filename pattern from the contract, matched on the BASENAME so
    burying it in a subdirectory doesn't get it through."""
    detail = _reject(auth_client, {"path": path, "content_b64": b64("{}")})
    assert detail["error"] == "secret_filename"
    assert detail["path"] == path


@pytest.mark.parametrize(
    "label, payload",
    [
        ("anthropic", "sk-" + "ant-"),
        ("sk-proj", "OPENAI_KEY: sk-proj-"),
        ("AKIA", "aws_access_key_id = AKIA1234567890ABCDEF"),
        ("rsa-private-key", "-----" + "BE" + "GIN RSA PRIVATE KEY-----\nMIIE\n"),
        ("openssh-private-key", "-----" + "BE" + "GIN OPENSSH PRIVATE KEY-----\nb3Bl\n"),
        ("bare-private-key", "-----" + "BE" + "GIN PRIVATE KEY-----\nMIIE\n"),
        ("slack-bot", "xo" + "xb-"),
        ("slack-user", "xo" + "xp-"),
        ("xoxa", "xox" + "a-9999"),
        ("ghp", "gh token ghp_16CharactersOfNope"),
        ("phk", "PHI_HUB_TOKEN=phk_"),
        ("glsa", "GRAFANA_TOKEN=glsa_"),
        (
            "bearer-in-json",
            '{"headers": {"Authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.abc"}}',
        ),
        (
            "bearer-in-json-lowercase-key",
            '{"authorization": "bearer 0123456789abcdef"}',
        ),
        (
            "bearer-in-json-spaced",
            '{ "Authorization" :  "Bearer  sometokenvalue123" }',
        ),
    ],
)
def test_secret_content_patterns_are_refused(
    published_bundle: dict, auth_client: TestClient, label: str, payload: str
) -> None:
    """Every content pattern from the contract."""
    detail = _reject(auth_client, {"path": f"skills/{label}/SKILL.md", "content_b64": b64(payload)})
    assert detail["error"] == "secret_content", label
    assert detail["path"] == f"skills/{label}/SKILL.md"


def test_akia_pattern_needs_the_full_shape(published_bundle: dict, auth_client: TestClient) -> None:
    """``AKIA`` on its own is a false positive the contract's regex avoids —
    it requires 16 more upper/digit characters. Prose about AWS publishes fine."""
    resp = _publish(
        auth_client,
        {"path": "docs/aws.md", "content_b64": b64("Access keys start with AKIA and are secret.")},
    )
    assert resp.status_code == 201, resp.text


@pytest.mark.parametrize(
    "label, payload",
    [
        ("dollar-env", '{"headers": {"Authorization": "Bearer $FEADS_TOKEN"}}'),
        ("braced-env", '{"headers": {"Authorization": "Bearer ${FEADS_TOKEN}"}}'),
        ("angle-placeholder", '{"headers": {"Authorization": "Bearer <token>"}}'),
        ("mustache", '{"headers": {"Authorization": "Bearer {{ env.TOKEN }}"}}'),
        ("empty", '{"headers": {"Authorization": "Bearer "}}'),
        ("prose", "Send the token as `Authorization: Bearer <token>` over HTTP."),
    ],
)
def test_bearer_placeholders_still_publish(
    published_bundle: dict, auth_client: TestClient, label: str, payload: str
) -> None:
    """The bearer guard must reject literal tokens without blocking the $ENV
    references a bundle is *supposed* to use to document its MCP wiring."""
    resp = _publish(
        auth_client, {"path": f"docs/{label}/mcp.json.example", "content_b64": b64(payload)}
    )
    assert resp.status_code == 201, resp.text


def test_mcp_json_example_is_publishable(published_bundle: dict, auth_client: TestClient) -> None:
    """Only the exact `mcp.json` basename is blocked — the `.example` a bundle
    ships to document its servers has a different basename and publishes fine."""
    resp = _publish(
        auth_client,
        {
            "path": "mcp.json.example",
            "content_b64": b64('{"servers": {"feads": {"url": "https://board.example.org"}}}'),
        },
    )
    assert resp.status_code == 201, resp.text


def test_ordinary_config_publishes_cleanly(published_bundle: dict, auth_client: TestClient) -> None:
    """The guard must not be so eager that a normal settings fragment trips it."""
    resp = _publish(
        auth_client,
        {"path": "settings/settings.json", "content_b64": b64('{"theme": "dark", "packages": []}')},
        {"path": "themes/midnight.json", "content_b64": b64('{"bg": "#150101"}')},
    )
    assert resp.status_code == 201, resp.text


def test_binary_content_is_stored_and_flagged(
    published_bundle: dict, auth_client: TestClient
) -> None:
    """Non-UTF-8 bytes round-trip, marked ``is_text: false`` so the UI knows
    not to try rendering them."""
    blob = base64.b64encode(bytes(range(256))).decode("ascii")
    resp = _publish(auth_client, {"path": "assets/icon.bin", "content_b64": blob})
    assert resp.status_code == 201, resp.text
    entry = next(f for f in resp.json()["files"] if f["path"] == "assets/icon.bin")
    assert entry["is_text"] is False
    assert entry["size"] == 256


# -------------------------------------------------------------------- slugs


@pytest.mark.parametrize(
    "slug",
    ["a", "-leading", "UPPER", "has_underscore", "has space", "has.dot", "x" * 65, ""],
)
def test_invalid_slugs_are_refused(auth_client: TestClient, slug: str) -> None:
    resp = auth_client.post("/api/bundles", json={"slug": slug, "title": "T"})
    # Pydantic's length bounds catch the extremes before our regex does;
    # either way the caller gets a 422 and never a created row.
    assert resp.status_code == 422, slug


@pytest.mark.parametrize("slug", ["ab", "a1", "my-cool-thing", "0-start", "x" * 64])
def test_valid_slugs_are_accepted(auth_client: TestClient, slug: str) -> None:
    assert auth_client.post("/api/bundles", json={"slug": slug, "title": "T"}).status_code == 201


# ------------------------------------------------------ canonical models.json
#
# README.md -> "Model bundles". The single models.json basename the hub
# accepts is the canonical models/models.json; it must parse as a JSON object
# with a providers object and carry no literal credential at any provider/model
# depth. Env/command references pass; a literal is refused and nothing stored.

_MODELS_OK = {
    "providers": {
        "example": {
            "name": "example Gateway",
            "baseUrl": "https://llm.example.org",
            "api": "openai-completions",
            "authHeader": True,
            "apiKey": "$example_API_KEY",
            "models": [
                {"id": "opus", "name": "opus", "contextWindow": 1000000, "maxTokens": 16384},
            ],
        }
    }
}


def _models_file(obj: object) -> dict:
    return {"path": "models/models.json", "content_b64": b64(json.dumps(obj))}


def test_canonical_models_json_with_env_ref_publishes(
    published_bundle: dict, auth_client: TestClient
) -> None:
    """The one models.json the hub accepts: canonical path, providers object,
    every credential an environment reference."""
    resp = _publish(auth_client, _models_file(_MODELS_OK))
    assert resp.status_code == 201, resp.text
    assert any(f["path"] == "models/models.json" for f in resp.json()["files"])


@pytest.mark.parametrize(
    "apikey",
    [
        "${example_API_KEY}",
        "!op read 'op:" + "//vault/example/key'",
        "!security find-generic-password -ws example",
        "$$literal-dollar",
        "<your-key-here>",
        "{{ env.example_API_KEY }}",
        "${KEY_PREFIX}_${KEY_SUFFIX}",
    ],
)
def test_canonical_models_json_permits_env_and_command_forms(
    published_bundle: dict, auth_client: TestClient, apikey: str
) -> None:
    """Every pi value-resolution form is a reference, not a secret -- all pass,
    and the !command forms are recognised, never executed."""
    obj = json.loads(json.dumps(_MODELS_OK))
    obj["providers"]["example"]["apiKey"] = apikey
    resp = _publish(auth_client, _models_file(obj))
    assert resp.status_code == 201, resp.text


@pytest.mark.parametrize(
    "path",
    [
        "models.json",
        "settings/models.json",
        "models/nested/models.json",
    ],
)
def test_non_canonical_models_json_is_still_refused(
    published_bundle: dict, auth_client: TestClient, path: str
) -> None:
    """Only the exact models/models.json path is exempt -- a raw one holds live
    keys, so every other models.json basename stays on the guard."""
    detail = _reject(auth_client, {"path": path, "content_b64": b64(json.dumps(_MODELS_OK))})
    assert detail["error"] == "secret_filename"
    assert detail["path"] == path


def test_canonical_models_json_rejects_invalid_json(
    published_bundle: dict, auth_client: TestClient
) -> None:
    detail = _reject(auth_client, {"path": "models/models.json", "content_b64": b64("{not json")})
    assert detail["error"] == "invalid_json"
    assert detail["path"] == "models/models.json"


@pytest.mark.parametrize("payload", ['["a", "b"]', '"a string"', "42"])
def test_canonical_models_json_must_be_an_object(
    published_bundle: dict, auth_client: TestClient, payload: str
) -> None:
    detail = _reject(auth_client, {"path": "models/models.json", "content_b64": b64(payload)})
    assert detail["error"] == "invalid_json"


@pytest.mark.parametrize(
    "obj",
    [
        {"models": []},
        {"providers": []},
        {"providers": "example"},
    ],
)
def test_canonical_models_json_needs_a_providers_object(
    published_bundle: dict, auth_client: TestClient, obj: object
) -> None:
    detail = _reject(auth_client, _models_file(obj))
    assert detail["error"] == "invalid_models_schema"
    assert detail["path"] == "models/models.json"


# fmt: off
# Keep synthetic credential shape split for gitleaks.
@pytest.mark.parametrize(
    "mutate",
    [
        lambda o: o["providers"]["example"].__setitem__("apiKey",
            "sk-" + "ant-"),
        lambda o: o["providers"]["example"].__setitem__("apiKey", "my-literal-secret-value"),
        lambda o: o["providers"]["example"].__setitem__(
            "headers", {"Authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.abcdef"}
        ),
        lambda o: o["providers"]["example"]["models"][0].__setitem__(
            "apiKey", "ghp_"
        ),
    ],
)
def test_canonical_models_json_rejects_literal_credentials_at_any_depth(
    published_bundle: dict, auth_client: TestClient, mutate
) -> None:
    """A literal credential at provider OR model depth, whether a known shape or
    just a bare string under a credential key, is refused as secret_content."""
    obj = json.loads(json.dumps(_MODELS_OK))
    mutate(obj)
    detail = _reject(auth_client, _models_file(obj))
    assert detail["error"] == "secret_content"
    assert detail["path"] == "models/models.json"


def test_models_kind_bundle_publishes_end_to_end(auth_client: TestClient) -> None:
    """A kind: models bundle is a first-class citizen -- create, publish its
    canonical catalog, and read it back."""
    created = auth_client.post(
        "/api/bundles",
        json={"slug": "gateway-models", "title": "Gateway models", "kind": "models"},
    )
    assert created.status_code == 201, created.text
    assert created.json()["kind"] == "models"
    published = auth_client.post(
        "/api/bundles/dev/gateway-models/versions",
        json={"changelog": "catalog", "files": [_models_file(_MODELS_OK)]},
    )
    assert published.status_code == 201, published.text
    files = auth_client.get("/api/bundles/dev/gateway-models/versions/latest/files").json()
    entry = next(f for f in files["files"] if f["path"] == "models/models.json")
    assert base64.b64decode(entry["content_b64"]) == json.dumps(_MODELS_OK).encode()
