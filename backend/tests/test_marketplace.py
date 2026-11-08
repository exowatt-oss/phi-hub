"""Marketplace (v2): org seeding, publisher authz, roster, update checking.

The contract's org layer is a *permission* feature, so most of what is
worth testing here is what must NOT work: a random colleague editing an
org bundle, a `phk_` token being treated differently from a browser
session, an org losing its last publisher, a soft-deleted bundle still
looking installable.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import orgs
from app.db import SessionLocal
from app.models.org import OrgPublisher
from app.models.user import User
from tests.conftest import b64

# The seed (conftest pins ORG_INITIAL_PUBLISHERS) gives `publisher` publish
# rights on `example`. `dev` and `other` are ordinary colleagues.
ORG = "example"


def _org_bundle(publisher: TestClient, slug: str = "org-kit", **extra) -> dict:
    """A bundle owned by the org, created by a publisher."""
    resp = publisher.post(
        "/api/bundles",
        json={"slug": slug, "title": "Org kit", "owner": ORG, **extra},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# --------------------------------------------------------------------- seed


def test_seed_creates_the_org_profile_and_its_initial_publishers(
    publisher_client: TestClient,
) -> None:
    profile = publisher_client.get(f"/api/profiles/{ORG}")
    assert profile.status_code == 200, profile.text
    assert profile.json()["profile"]["is_org"] is True

    roster = publisher_client.get(f"/api/orgs/{ORG}/publishers").json()
    assert [p["handle"] for p in roster["publishers"]] == ["publisher"]
    assert roster["is_publisher"] is True
    # Seeded grants have no granter — nobody clicked a button.
    assert roster["publishers"][0]["added_by"] is None


def test_seed_is_idempotent_across_repeated_runs(auth_client: TestClient) -> None:
    """The seed runs on every boot and from every worker. Running it three
    more times must not duplicate the org, the stub, or the grant."""
    with SessionLocal() as db:
        for _ in range(3):
            orgs.ensure_seed(db)

    with SessionLocal() as db:
        orgs_found = db.execute(select(User).where(User.is_org.is_(True))).scalars().all()
        assert [o.handle for o in orgs_found] == [ORG]
        grants = db.execute(select(OrgPublisher)).scalars().all()
        assert len(grants) == 1
        stubs = (
            db.execute(select(User).where(User.email == "publisher@example.com")).scalars().all()
        )
        assert len(stubs) == 1


def test_seeded_publisher_is_a_pending_stub_until_they_sign_in(
    auth_client: TestClient, publisher_client: TestClient
) -> None:
    """A seeded publisher has a profile but has never been active. It must
    still be a real, grantable profile — and it must stop reading as
    "pending" the moment they actually show up."""
    before = auth_client.get(f"/api/orgs/{ORG}/publishers").json()["publishers"][0]
    assert before["handle"] == "publisher"
    # Seeded, never signed in — an invite typo'd into a nonexistent address
    # looks exactly like this forever, so it has to be visible.
    assert before["pending"] is True
    # `publisher_client` authenticates as publisher@example.com, which lands on
    # the seeded stub rather than creating a second row.
    assert publisher_client.get("/api/me").json()["profile"]["handle"] == "publisher"
    after = auth_client.get(f"/api/orgs/{ORG}/publishers").json()["publishers"][0]
    assert after["pending"] is False
    assert after["handle"] == "publisher"


def test_the_org_never_takes_the_bootstrap_admin_grant(publisher_client: TestClient) -> None:
    """`is_admin` goes to the first HUMAN through the door, not to a row the
    seed conjured before anyone signed in."""
    assert publisher_client.get("/api/auth/me").json()["is_admin"] is True
    with SessionLocal() as db:
        org = db.execute(select(User).where(User.is_org.is_(True))).scalar_one()
        assert org.is_admin is False
        assert org.last_sign_in_at is None


# ------------------------------------------------------------ publish owner


def test_a_publisher_can_create_a_bundle_under_the_org(publisher_client: TestClient) -> None:
    body = _org_bundle(publisher_client)
    assert body["ref"] == f"{ORG}/org-kit"
    assert body["owner"] == ORG


def test_a_non_publisher_cannot_create_under_the_org(auth_client: TestClient) -> None:
    resp = auth_client.post("/api/bundles", json={"slug": "sneaky", "title": "X", "owner": ORG})
    assert resp.status_code == 403
    assert "not a publisher" in resp.json()["detail"]
    assert auth_client.get("/api/bundles").json()["total"] == 0


def test_owner_pointing_at_a_nonexistent_org_is_404(publisher_client: TestClient) -> None:
    resp = publisher_client.post(
        "/api/bundles", json={"slug": "orphan", "title": "X", "owner": "no-such-org"}
    )
    assert resp.status_code == 404


def test_owner_pointing_at_a_person_is_404_not_a_hijack(
    publisher_client: TestClient, auth_client: TestClient
) -> None:
    """`owner` names an ORG. A human handle must not be a way to publish
    into somebody else's namespace."""
    auth_client.get("/api/me")  # make sure `dev` exists
    resp = publisher_client.post(
        "/api/bundles", json={"slug": "hijack", "title": "X", "owner": "dev"}
    )
    assert resp.status_code == 404
    assert auth_client.get("/api/bundles?owner=dev").json()["total"] == 0


def test_owner_set_to_your_own_handle_is_a_plain_self_publish(auth_client: TestClient) -> None:
    resp = auth_client.post("/api/bundles", json={"slug": "mine", "title": "M", "owner": "dev"})
    assert resp.status_code == 201
    assert resp.json()["ref"] == "dev/mine"


# -------------------------------------------------------------- write authz


def test_publisher_authz_matrix(
    publisher_client: TestClient, auth_client: TestClient, other_client: TestClient
) -> None:
    """Owner (the org, via any publisher) writes; everyone else 403s on
    every owner-scoped route."""
    _org_bundle(publisher_client)
    ref = f"/api/bundles/{ORG}/org-kit"

    # Reads stay org-wide.
    for client in (publisher_client, auth_client, other_client):
        assert client.get(ref).status_code == 200

    # A publisher may edit, publish and delete.
    assert publisher_client.patch(ref, json={"title": "Renamed"}).status_code == 200
    assert (
        publisher_client.post(
            f"{ref}/versions", json={"files": [{"path": "a.md", "content_b64": b64("x")}]}
        ).status_code
        == 201
    )

    # A non-publisher may do none of it.
    for client in (auth_client, other_client):
        assert client.patch(ref, json={"title": "Hijack"}).status_code == 403
        assert (
            client.post(
                f"{ref}/versions", json={"files": [{"path": "b.md", "content_b64": b64("x")}]}
            ).status_code
            == 403
        )
        assert client.delete(ref).status_code == 403

    # The 403 names the publisher route, not just "owner" — a colleague who
    # should be a publisher needs to know that is the fix.
    assert "publisher" in auth_client.patch(ref, json={"title": "x"}).json()["detail"]

    # Nothing a non-publisher tried actually landed.
    detail = auth_client.get(ref).json()
    assert detail["bundle"]["title"] == "Renamed"
    assert detail["bundle"]["latest_version"] == 1

    assert publisher_client.delete(ref).status_code == 204


def test_personal_bundles_keep_the_plain_owner_only_message(
    published_bundle: dict, other_client: TestClient
) -> None:
    """The org wording must not leak onto personal bundles, where being a
    publisher of anything is irrelevant."""
    detail = other_client.patch("/api/bundles/dev/routing-setup", json={"title": "x"}).json()
    assert detail["detail"] == "Only the bundle's owner can change it."


def test_publisher_authz_is_identical_over_a_phk_token(
    publisher_client: TestClient, client: TestClient
) -> None:
    """A phi CLI presenting a `phk_` token is the same principal as the
    browser session that minted it — publisher rights must travel with it,
    and a random user's token must not acquire them."""
    _org_bundle(publisher_client)
    ref = f"/api/bundles/{ORG}/org-kit"

    publisher_token = publisher_client.post("/api/tokens", json={"name": "cli"}).json()["token"]
    client.headers.update({"Authorization": f"Bearer {publisher_token}"})
    assert client.patch(ref, json={"title": "Via token"}).status_code == 200

    # Now a token belonging to somebody who is not a publisher.
    outsider = TestClient(client.app)
    outsider.headers.update({"X-Auth-Request-Email": "other@example.com"})
    outsider_token = outsider.post("/api/tokens", json={"name": "cli"}).json()["token"]
    outsider.headers.clear()
    outsider.headers.update({"Authorization": f"Bearer {outsider_token}"})
    assert outsider.patch(ref, json={"title": "Nope"}).status_code == 403


def test_can_write_is_reported_separately_from_is_owner(
    publisher_client: TestClient, auth_client: TestClient
) -> None:
    """The UI gates Edit/Delete on `can_write`; `is_owner` keeps meaning
    literally "you are the owner" so nothing built against it shifts."""
    _org_bundle(publisher_client)
    ref = f"/api/bundles/{ORG}/org-kit"

    mine = publisher_client.get(ref).json()
    assert mine["is_owner"] is False  # the ORG owns it, not publisher
    assert mine["can_write"] is True

    theirs = auth_client.get(ref).json()
    assert theirs["is_owner"] is False
    assert theirs["can_write"] is False


def test_version_author_is_the_person_not_the_org(publisher_client: TestClient) -> None:
    """`created_by` has to survive a publisher leaving the org."""
    _org_bundle(publisher_client)
    publisher_client.post(
        f"/api/bundles/{ORG}/org-kit/versions",
        json={"changelog": "first", "files": [{"path": "a.md", "content_b64": b64("x")}]},
    )
    versions = publisher_client.get(f"/api/bundles/{ORG}/org-kit").json()["versions"]
    assert versions[0]["created_by"] == "publisher"


# ------------------------------------------------------- publisher endpoints


def test_anyone_may_read_the_roster_but_only_publishers_may_change_it(
    auth_client: TestClient,
) -> None:
    roster = auth_client.get(f"/api/orgs/{ORG}/publishers")
    assert roster.status_code == 200
    assert roster.json()["is_publisher"] is False

    assert (
        auth_client.post(
            f"/api/orgs/{ORG}/publishers", json={"email": "dev@example.com"}
        ).status_code
        == 403
    )
    assert auth_client.delete(f"/api/orgs/{ORG}/publishers/publisher").status_code == 403


def test_adding_a_publisher_by_email_creates_the_profile_stub(
    publisher_client: TestClient,
) -> None:
    """Contract: you should not have to make someone visit the hub before
    you can grant them anything."""
    resp = publisher_client.post(
        f"/api/orgs/{ORG}/publishers", json={"email": "Newbie@example.com"}
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    added = {p["handle"]: p for p in body["publishers"]}
    assert set(added) == {"publisher", "newbie"}
    assert added["newbie"]["email"] == "newbie@example.com"
    assert added["newbie"]["pending"] is True
    assert added["newbie"]["added_by"] == "publisher"

    # The stub is a real profile the rest of the app can see.
    assert publisher_client.get("/api/profiles/newbie").status_code == 200


def test_adding_an_existing_publisher_is_an_idempotent_no_op(
    publisher_client: TestClient,
) -> None:
    first = publisher_client.post(f"/api/orgs/{ORG}/publishers", json={"email": "a@example.com"})
    second = publisher_client.post(f"/api/orgs/{ORG}/publishers", json={"email": "a@example.com"})
    assert second.status_code == 201
    assert first.json()["publishers"] == second.json()["publishers"]


def test_a_new_publisher_can_immediately_publish_for_the_org(
    publisher_client: TestClient, auth_client: TestClient
) -> None:
    body = {"slug": "fresh", "title": "X", "owner": ORG}
    assert auth_client.post("/api/bundles", json=body).status_code == 403
    publisher_client.post(f"/api/orgs/{ORG}/publishers", json={"email": "dev@example.com"})
    assert auth_client.post("/api/bundles", json=body).status_code == 201


def test_removing_a_publisher_revokes_write_access(
    publisher_client: TestClient, auth_client: TestClient
) -> None:
    publisher_client.post(f"/api/orgs/{ORG}/publishers", json={"email": "dev@example.com"})
    _org_bundle(auth_client)
    ref = f"/api/bundles/{ORG}/org-kit"
    assert auth_client.patch(ref, json={"title": "Fine"}).status_code == 200

    removed = publisher_client.delete(f"/api/orgs/{ORG}/publishers/dev")
    assert removed.status_code == 200
    assert [p["handle"] for p in removed.json()["publishers"]] == ["publisher"]
    # The bundle stays with the org; the ex-publisher just cannot touch it.
    assert auth_client.patch(ref, json={"title": "Not any more"}).status_code == 403
    assert auth_client.get(ref).json()["bundle"]["title"] == "Fine"


def test_the_last_publisher_cannot_be_removed(publisher_client: TestClient) -> None:
    """An org with an empty roster owns bundles nobody can ever update, and
    there is no admin path back."""
    resp = publisher_client.delete(f"/api/orgs/{ORG}/publishers/publisher")
    assert resp.status_code == 409
    assert "at least one publisher" in resp.json()["detail"]
    assert [
        p["handle"]
        for p in publisher_client.get(f"/api/orgs/{ORG}/publishers").json()["publishers"]
    ] == ["publisher"]


def test_a_publisher_may_remove_themselves_once_someone_else_holds_the_keys(
    publisher_client: TestClient,
) -> None:
    publisher_client.post(f"/api/orgs/{ORG}/publishers", json={"email": "dev@example.com"})
    resp = publisher_client.delete(f"/api/orgs/{ORG}/publishers/publisher")
    assert resp.status_code == 200
    assert resp.json()["is_publisher"] is False
    assert [p["handle"] for p in resp.json()["publishers"]] == ["dev"]


def test_removing_someone_who_is_not_a_publisher_is_404(publisher_client: TestClient) -> None:
    publisher_client.post(f"/api/orgs/{ORG}/publishers", json={"email": "dev@example.com"})
    assert publisher_client.delete(f"/api/orgs/{ORG}/publishers/nobody").status_code == 404


def test_org_directory_reports_the_callers_own_publish_rights(
    publisher_client: TestClient, auth_client: TestClient
) -> None:
    """What the "publish as" picker is built on — the caller must not have
    to probe the roster endpoint once per org to find out."""
    as_publisher = publisher_client.get("/api/orgs").json()
    assert [o["profile"]["handle"] for o in as_publisher] == [ORG]
    assert as_publisher[0]["is_publisher"] is True
    assert as_publisher[0]["publisher_count"] == 1
    assert as_publisher[0]["profile"]["is_org"] is True

    assert auth_client.get("/api/orgs").json()[0]["is_publisher"] is False


def test_publishers_of_an_unknown_org_is_404(auth_client: TestClient) -> None:
    assert auth_client.get("/api/orgs/nope/publishers").status_code == 404


def test_a_person_handle_is_not_an_org(published_bundle: dict, auth_client: TestClient) -> None:
    assert auth_client.get("/api/orgs/dev/publishers").status_code == 404


def test_an_org_cannot_be_made_a_publisher(publisher_client: TestClient) -> None:
    org_email = publisher_client.get(f"/api/profiles/{ORG}").json()["profile"]["email"]
    resp = publisher_client.post(f"/api/orgs/{ORG}/publishers", json={"email": org_email})
    assert resp.status_code == 422


def test_a_junk_email_is_refused_rather_than_stubbed(publisher_client: TestClient) -> None:
    assert (
        publisher_client.post(f"/api/orgs/{ORG}/publishers", json={"email": "not-an-email"})
    ).status_code == 422


# ------------------------------------------------------------- org_only view


def test_org_only_filters_to_org_owned_bundles(
    publisher_client: TestClient, auth_client: TestClient
) -> None:
    _org_bundle(publisher_client)
    auth_client.post("/api/bundles", json={"slug": "personal", "title": "Personal"})

    everything = auth_client.get("/api/bundles").json()
    assert everything["total"] == 2

    curated = auth_client.get("/api/bundles?org_only=true").json()
    assert [b["ref"] for b in curated["bundles"]] == [f"{ORG}/org-kit"]
    assert curated["total"] == 1

    # It composes with the other filters rather than replacing them.
    assert auth_client.get("/api/bundles?org_only=true&kind=theme").json()["total"] == 0
    assert auth_client.get("/api/bundles?org_only=false").json()["total"] == 2


def test_org_only_excludes_soft_deleted_org_bundles(publisher_client: TestClient) -> None:
    _org_bundle(publisher_client)
    publisher_client.delete(f"/api/bundles/{ORG}/org-kit")
    assert publisher_client.get("/api/bundles?org_only=true").json()["total"] == 0


# ------------------------------------------------------------ updates/check


def test_update_check_classifies_current_stale_and_missing(
    published_bundle: dict, auth_client: TestClient
) -> None:
    """dev/routing-setup is at v1. Ask about it three ways at once."""
    auth_client.post(
        "/api/bundles/dev/routing-setup/versions",
        json={"changelog": "second cut", "files": [{"path": "a.md", "content_b64": b64("x")}]},
    )
    auth_client.post("/api/bundles", json={"slug": "steady", "title": "Steady"})
    auth_client.post(
        "/api/bundles/dev/steady/versions",
        json={"files": [{"path": "a.md", "content_b64": b64("x")}]},
    )

    body = auth_client.post(
        "/api/updates/check",
        json={
            "installed": [
                {"ref": "dev/routing-setup", "version": 1},  # stale → update
                {"ref": "dev/steady", "version": 1},  # current → neither list
                {"ref": "dev/vanished", "version": 4},  # missing
            ]
        },
    ).json()

    assert body["checked"] == 3
    assert [u["ref"] for u in body["updates"]] == ["dev/routing-setup"]
    update = body["updates"][0]
    assert update["latest_version"] == 2
    assert update["changelog"] == "second cut"
    assert update["title"] == "Routing setup"
    assert update["kind"] == "settings"
    assert update["is_org"] is False
    assert body["missing"] == ["dev/vanished"]


def test_update_check_on_an_empty_hub_is_two_empty_lists(auth_client: TestClient) -> None:
    body = auth_client.post("/api/updates/check", json={"installed": []}).json()
    assert body == {"updates": [], "missing": [], "checked": 0}


def test_soft_deleted_bundles_count_as_missing(
    published_bundle: dict, auth_client: TestClient, other_client: TestClient
) -> None:
    """Someone holding a deleted bundle must be told to prune it, not left
    polling a ref that will never move again."""
    auth_client.delete("/api/bundles/dev/routing-setup")
    body = other_client.post(
        "/api/updates/check", json={"installed": [{"ref": "dev/routing-setup", "version": 1}]}
    ).json()
    assert body["updates"] == []
    assert body["missing"] == ["dev/routing-setup"]


def test_a_malformed_ref_is_missing_not_a_400(auth_client: TestClient) -> None:
    """One fat-fingered entry must not fail an entire startup check."""
    body = auth_client.post(
        "/api/updates/check",
        json={"installed": [{"ref": "not-a-ref", "version": 1}, {"ref": "a/b/c", "version": 0}]},
    )
    assert body.status_code == 200
    assert body.json()["updates"] == []
    assert "not-a-ref" in body.json()["missing"]


def test_update_check_reports_a_first_version_to_a_version_zero_holder(
    published_bundle: dict, auth_client: TestClient
) -> None:
    """A client that has the bundle linked but never resolved a version is
    behind, not current."""
    body = auth_client.post(
        "/api/updates/check", json={"installed": [{"ref": "dev/routing-setup", "version": 0}]}
    ).json()
    assert body["updates"][0]["latest_version"] == 1


def test_update_check_flags_org_ownership_for_the_auto_update_rule(
    publisher_client: TestClient,
) -> None:
    """phi's default `hubAutoUpdate: "org"` applies org updates silently and
    only notifies for personal ones, so `is_org` has to travel with each."""
    _org_bundle(publisher_client)
    publisher_client.post(
        f"/api/bundles/{ORG}/org-kit/versions",
        json={"files": [{"path": "a.md", "content_b64": b64("x")}]},
    )
    body = publisher_client.post(
        "/api/updates/check", json={"installed": [{"ref": f"{ORG}/org-kit", "version": 0}]}
    ).json()
    assert body["updates"][0]["is_org"] is True


def test_update_check_tolerates_a_pinned_ref_suffix(
    published_bundle: dict, auth_client: TestClient
) -> None:
    body = auth_client.post(
        "/api/updates/check", json={"installed": [{"ref": "dev/routing-setup@v1", "version": 0}]}
    ).json()
    assert [u["ref"] for u in body["updates"]] == ["dev/routing-setup"]


def test_update_check_requires_identity(client: TestClient) -> None:
    assert client.post("/api/updates/check", json={"installed": []}).status_code == 401


def test_update_check_caps_the_batch(auth_client: TestClient) -> None:
    """A bounded IN-list is what keeps this one query."""
    installed = [{"ref": f"dev/b{i}", "version": 1} for i in range(501)]
    assert auth_client.post("/api/updates/check", json={"installed": installed}).status_code == 422
