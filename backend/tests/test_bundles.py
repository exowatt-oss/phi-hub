"""Bundle CRUD, versions, files, archive, events and install counting."""

from __future__ import annotations

import gzip
import io
import tarfile

from fastapi.testclient import TestClient

from tests.conftest import b64

# ------------------------------------------------------------------- CRUD


def test_create_read_update_delete(auth_client: TestClient) -> None:
    created = auth_client.post(
        "/api/bundles",
        json={
            "slug": "review-kit",
            "title": "Review kit",
            "description": "PR review prompts",
            "kind": "prompt",
            "tags": ["Review", "review", "  ", "pr"],
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["ref"] == "dev/review-kit"
    # Tags are lowercased, de-duplicated and blank-stripped server-side.
    assert body["tags"] == ["review", "pr"]
    assert body["latest_version"] == 0

    patched = auth_client.patch(
        "/api/bundles/dev/review-kit", json={"title": "Review kit v2", "kind": "mixed"}
    )
    assert patched.status_code == 200
    assert patched.json()["bundle"]["title"] == "Review kit v2"
    assert patched.json()["bundle"]["kind"] == "mixed"

    assert auth_client.delete("/api/bundles/dev/review-kit").status_code == 204
    assert auth_client.get("/api/bundles/dev/review-kit").status_code == 404
    # Soft delete: it's out of the listing too.
    assert auth_client.get("/api/bundles").json()["total"] == 0


def test_duplicate_slug_per_owner_is_409(auth_client: TestClient) -> None:
    auth_client.post("/api/bundles", json={"slug": "dupe", "title": "One"})
    resp = auth_client.post("/api/bundles", json={"slug": "dupe", "title": "Two"})
    assert resp.status_code == 409


def test_same_slug_under_different_owners_is_fine(
    auth_client: TestClient, other_client: TestClient
) -> None:
    """Slugs are unique PER OWNER — `owner/slug` is the real identifier."""
    assert (
        auth_client.post("/api/bundles", json={"slug": "shared", "title": "Mine"}).status_code
        == 201
    )
    assert (
        other_client.post("/api/bundles", json={"slug": "shared", "title": "Theirs"}).status_code
        == 201
    )
    assert auth_client.get("/api/bundles").json()["total"] == 2


def test_only_the_owner_can_write(published_bundle: dict, other_client: TestClient) -> None:
    """Reads are org-wide; writes are not."""
    assert other_client.get("/api/bundles/dev/routing-setup").status_code == 200
    assert (
        other_client.patch("/api/bundles/dev/routing-setup", json={"title": "Hijack"}).status_code
        == 403
    )
    assert other_client.delete("/api/bundles/dev/routing-setup").status_code == 403
    assert (
        other_client.post(
            "/api/bundles/dev/routing-setup/versions",
            json={"files": [{"path": "x.md", "content_b64": b64("x")}]},
        ).status_code
        == 403
    )


def test_missing_bundle_is_404(auth_client: TestClient) -> None:
    assert auth_client.get("/api/bundles/nobody/nothing").status_code == 404


# --------------------------------------------------------------- versions


def test_versions_increment_and_are_immutable(
    published_bundle: dict, auth_client: TestClient
) -> None:
    second = auth_client.post(
        "/api/bundles/dev/routing-setup/versions",
        json={"changelog": "second cut", "files": [{"path": "a.md", "content_b64": b64("v2")}]},
    )
    assert second.status_code == 201
    assert second.json()["version"]["version"] == 2

    detail = auth_client.get("/api/bundles/dev/routing-setup").json()
    # Newest first, so the changelog reads like a release feed.
    assert [v["version"] for v in detail["versions"]] == [2, 1]
    assert detail["bundle"]["latest_version"] == 2
    # `latest` follows the new one; v1's files are untouched.
    assert [f["path"] for f in detail["files"]] == ["a.md"]
    v1 = auth_client.get("/api/bundles/dev/routing-setup/versions/1").json()
    assert [f["path"] for f in v1["files"]] == ["settings/settings.json", "skills/routing/SKILL.md"]


def test_version_metadata_is_recorded(published_bundle: dict, auth_client: TestClient) -> None:
    v1 = auth_client.get("/api/bundles/dev/routing-setup/versions/latest").json()["version"]
    assert v1["version"] == 1
    assert v1["changelog"] == "first cut"
    assert v1["file_count"] == 2
    assert v1["total_bytes"] == len("# routing\n") + len('{"model": "opus"}')
    assert v1["created_by"] == "dev"


def test_unknown_version_is_404(published_bundle: dict, auth_client: TestClient) -> None:
    assert auth_client.get("/api/bundles/dev/routing-setup/versions/99").status_code == 404
    assert auth_client.get("/api/bundles/dev/routing-setup/versions/abc").status_code == 400


def test_files_endpoint_round_trips_content(
    published_bundle: dict, auth_client: TestClient
) -> None:
    """This is the endpoint the phi extension materializes an import from,
    so the bytes must come back exactly as published."""
    import base64

    body = auth_client.get("/api/bundles/dev/routing-setup/versions/latest/files").json()
    assert body["ref"] == "dev/routing-setup"
    assert body["version"] == 1
    by_path = {f["path"]: f for f in body["files"]}
    assert base64.b64decode(by_path["skills/routing/SKILL.md"]["content_b64"]) == b"# routing\n"
    assert by_path["settings/settings.json"]["is_text"] is True


def test_search_envelope_serves_both_bundles_and_items(
    published_bundle: dict, auth_client: TestClient
) -> None:
    """The contract names the endpoint but not its envelope key, and the two
    clients built against it read different ones. Both must work, and they
    must never drift — a client reading the wrong key would silently render
    an empty registry with no error anywhere."""
    body = auth_client.get("/api/bundles").json()
    assert body["bundles"] == body["items"]
    assert body["total"] == len(body["bundles"]) == 1
    assert body["bundles"][0]["ref"] == "dev/routing-setup"


def test_detail_hoists_ref_and_latest_version(
    published_bundle: dict, auth_client: TestClient
) -> None:
    """A materializing client builds a ``v<N>`` store path from the version
    number; ``detail.latest_version`` is the obvious place to look for it."""
    body = auth_client.get("/api/bundles/dev/routing-setup").json()
    assert body["ref"] == body["bundle"]["ref"] == "dev/routing-setup"
    assert body["latest_version"] == body["bundle"]["latest_version"] == 1


def test_latest_literal_works_on_every_versioned_route(
    published_bundle: dict, auth_client: TestClient
) -> None:
    """`latest` is accepted wherever an integer version is, and resolves to
    exactly the same payload."""
    base = "/api/bundles/dev/routing-setup/versions"
    for suffix in ("", "/files"):
        by_latest = auth_client.get(f"{base}/latest{suffix}")
        by_number = auth_client.get(f"{base}/1{suffix}")
        assert by_latest.status_code == 200, suffix
        assert by_latest.json() == by_number.json(), suffix
    assert auth_client.get(f"{base}/latest/archive.tgz").status_code == 200


def test_every_files_entry_has_path_and_content_b64(
    published_bundle: dict, auth_client: TestClient
) -> None:
    """The minimum the phi extension materializes from."""
    body = auth_client.get("/api/bundles/dev/routing-setup/versions/latest/files").json()
    assert body["version"] == 1
    assert body["files"]
    for entry in body["files"]:
        assert entry["path"] and isinstance(entry["path"], str)
        assert isinstance(entry["content_b64"], str)


def test_pi_manifest_round_trips_to_the_materializing_client(auth_client: TestClient) -> None:
    """A bundle is a pi package, so the client has to be able to write
    ``package.json`` back out — the files endpoint carries the manifest."""
    manifest = {"name": "routing", "pi": {"skills": ["skills/routing"]}}
    auth_client.post("/api/bundles", json={"slug": "with-manifest", "title": "M"})
    auth_client.post(
        "/api/bundles/dev/with-manifest/versions",
        json={
            "pi_manifest": manifest,
            "files": [{"path": "skills/routing/SKILL.md", "content_b64": b64("x")}],
        },
    )
    files = auth_client.get("/api/bundles/dev/with-manifest/versions/latest/files").json()
    assert files["pi_manifest"] == manifest
    detail = auth_client.get("/api/bundles/dev/with-manifest").json()
    assert detail["versions"][0]["pi_manifest"] == manifest


def test_archive_is_a_valid_deterministic_tarball(
    published_bundle: dict, auth_client: TestClient
) -> None:
    resp = auth_client.get("/api/bundles/dev/routing-setup/versions/1/archive.tgz")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/gzip"
    assert "dev-routing-setup-v1.tgz" in resp.headers["content-disposition"]

    with tarfile.open(fileobj=io.BytesIO(resp.content), mode="r:gz") as tar:
        names = sorted(tar.getnames())
    # Everything under one root directory — an unpack in the wrong cwd
    # can't scatter files across the user's tree.
    assert names == [
        "dev-routing-setup-v1/settings/settings.json",
        "dev-routing-setup-v1/skills/routing/SKILL.md",
    ]

    # Byte-identical on re-download: mtime and the gzip header are pinned
    # to the version's publish time, not "now".
    again = auth_client.get("/api/bundles/dev/routing-setup/versions/1/archive.tgz")
    assert again.content == resp.content
    assert gzip.decompress(again.content) == gzip.decompress(resp.content)


# ------------------------------------------------------- events + installs


def test_install_count_follows_the_latest_event_per_profile(
    published_bundle: dict, auth_client: TestClient, other_client: TestClient
) -> None:
    """The contract defines installs as "distinct profiles whose LATEST
    event is imported/enabled" — so this is a fold, not a running total."""
    ref = "/api/bundles/dev/routing-setup"

    def installs() -> int:
        return auth_client.get(ref).json()["bundle"]["install_count"]

    assert installs() == 0

    assert (
        other_client.post(f"{ref}/events", json={"action": "imported", "version": 1}).status_code
        == 201
    )
    assert installs() == 1

    # Re-importing the same profile doesn't double-count.
    other_client.post(f"{ref}/events", json={"action": "imported", "version": 1})
    assert installs() == 1

    # A second profile does.
    auth_client.post(f"{ref}/events", json={"action": "imported", "version": 1})
    assert installs() == 2

    # Disabling takes that profile back out...
    other_client.post(f"{ref}/events", json={"action": "disabled"})
    assert installs() == 1

    # ...and re-enabling puts it back.
    other_client.post(f"{ref}/events", json={"action": "enabled", "version": 1})
    assert installs() == 2

    # `removed` is also "not installed".
    other_client.post(f"{ref}/events", json={"action": "removed"})
    auth_client.post(f"{ref}/events", json={"action": "removed"})
    assert installs() == 0


def test_applied_does_not_count_as_installed(
    published_bundle: dict, other_client: TestClient, auth_client: TestClient
) -> None:
    """`applied` records a settings-fragment application, which is a
    separate act from holding the bundle."""
    ref = "/api/bundles/dev/routing-setup"
    other_client.post(f"{ref}/events", json={"action": "applied"})
    assert auth_client.get(ref).json()["bundle"]["install_count"] == 0


def test_invalid_event_action_is_422(published_bundle: dict, auth_client: TestClient) -> None:
    resp = auth_client.post("/api/bundles/dev/routing-setup/events", json={"action": "yolo"})
    assert resp.status_code == 422


def test_recent_events_are_surfaced(
    published_bundle: dict, other_client: TestClient, auth_client: TestClient
) -> None:
    other_client.post(
        "/api/bundles/dev/routing-setup/events", json={"action": "imported", "version": 1}
    )
    events = auth_client.get("/api/bundles/dev/routing-setup").json()["recent_events"]
    assert events[0]["action"] == "imported"
    assert events[0]["handle"] == "other"


# ----------------------------------------------------------- search / sort


def test_search_matches_slug_title_and_description(auth_client: TestClient) -> None:
    auth_client.post("/api/bundles", json={"slug": "alpha", "title": "Nothing", "description": "z"})
    auth_client.post(
        "/api/bundles", json={"slug": "beta", "title": "Routing helper", "description": "z"}
    )
    auth_client.post(
        "/api/bundles", json={"slug": "gamma", "title": "Z", "description": "about routing"}
    )

    assert auth_client.get("/api/bundles?q=alpha").json()["total"] == 1
    assert auth_client.get("/api/bundles?q=ROUTING").json()["total"] == 2
    assert auth_client.get("/api/bundles?q=nope").json()["total"] == 0


def test_filter_by_kind_and_owner(auth_client: TestClient, other_client: TestClient) -> None:
    auth_client.post("/api/bundles", json={"slug": "a-skill", "title": "S", "kind": "skill"})
    other_client.post("/api/bundles", json={"slug": "a-theme", "title": "T", "kind": "theme"})

    assert auth_client.get("/api/bundles?kind=skill").json()["total"] == 1
    assert auth_client.get("/api/bundles?owner=other").json()["bundles"][0]["slug"] == "a-theme"
    assert auth_client.get("/api/bundles?kind=theme&owner=dev").json()["total"] == 0


def test_sort_popular_ranks_by_installs_and_keeps_zero_install_bundles(
    auth_client: TestClient, other_client: TestClient
) -> None:
    """The install-count join must be a LEFT OUTER — a never-imported
    bundle still belongs in the results, just with a zero."""
    auth_client.post("/api/bundles", json={"slug": "quiet", "title": "Quiet"})
    auth_client.post("/api/bundles", json={"slug": "loud", "title": "Loud"})
    other_client.post("/api/bundles/dev/loud/events", json={"action": "imported"})

    listing = auth_client.get("/api/bundles?sort=popular").json()
    assert listing["total"] == 2
    assert [b["slug"] for b in listing["bundles"]] == ["loud", "quiet"]
    assert listing["bundles"][1]["install_count"] == 0


def test_publishing_bumps_updated_at_for_sorting(auth_client: TestClient) -> None:
    auth_client.post("/api/bundles", json={"slug": "first", "title": "First"})
    auth_client.post("/api/bundles", json={"slug": "second", "title": "Second"})
    auth_client.post(
        "/api/bundles/dev/first/versions",
        json={"files": [{"path": "a.md", "content_b64": b64("x")}]},
    )
    order = [b["slug"] for b in auth_client.get("/api/bundles?sort=updated").json()["bundles"]]
    assert order[0] == "first"


# ------------------------------------------------------------- detail body


def test_detail_carries_snippets_and_instructions(
    published_bundle: dict, auth_client: TestClient
) -> None:
    detail = auth_client.get("/api/bundles/dev/routing-setup").json()
    snippets = detail["import_snippets"]
    assert snippets["phi_command"] == "/hub import dev/routing-setup"
    assert "hub_get_bundle_files" in snippets["agent_prompt"]
    assert "/api/bundles/dev/routing-setup/versions/1/files" in snippets["curl"]

    instructions = detail["import_instructions"]
    assert instructions["store_path"] == "~/.pi/agent/hub/bundles/dev/routing-setup/v1"
    assert instructions["settings_path"] == "~/.pi/agent/settings.json"
    assert any("packages" in step for step in instructions["any_agent"]["steps"])
    assert detail["is_owner"] is True


def test_is_owner_is_false_for_everyone_else(
    published_bundle: dict, other_client: TestClient
) -> None:
    assert other_client.get("/api/bundles/dev/routing-setup").json()["is_owner"] is False


# --------------------------------------------------------------- profiles


def test_profile_directory_and_detail(
    published_bundle: dict, auth_client: TestClient, other_client: TestClient
) -> None:
    other_client.post(
        "/api/bundles/dev/routing-setup/events", json={"action": "imported", "version": 1}
    )

    directory = {p["handle"]: p for p in auth_client.get("/api/profiles").json()}
    assert directory["dev"]["bundle_count"] == 1
    assert directory["other"]["bundle_count"] == 0
    assert directory["other"]["import_count"] == 1

    detail = auth_client.get("/api/profiles/dev").json()
    assert detail["profile"]["handle"] == "dev"
    assert [b["slug"] for b in detail["bundles"]] == ["routing-setup"]


def test_unknown_profile_is_404(auth_client: TestClient) -> None:
    assert auth_client.get("/api/profiles/nobody").status_code == 404
