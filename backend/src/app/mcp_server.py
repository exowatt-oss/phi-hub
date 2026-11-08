"""phi hub MCP tools. Mounted at ``/mcp`` by ``main.py``.

The same identity resolver gates REST and MCP bundle reads and writes.
Deploy behind an authenticated proxy or use an app-issued ``phk_`` token.

Three things about this file are load-bearing:

1. **``build_mcp_app()`` is a factory, not a module singleton.**
   ``StreamableHTTPSessionManager.run()`` may only be called once per
   instance, and the test suite builds a fresh app per test — a
   module-level server would 500 the second MCP-touching test with
   "run() can only be called once per instance".
2. **The parent app must run this sub-app's lifespan.** A mounted
   sub-app's lifespan does NOT run under the parent, so the session
   manager's task group is never started and every ``/mcp`` request
   500s with "Task group is not initialized". ``main.py``'s ``_lifespan``
   nests ``app.state.mcp_app.router.lifespan_context``.
3. **Identity comes from the request headers, not a FastAPI dependency.**
   Tools take a ``Context`` and read ``ctx.headers``, then hand them to
   the same ``deps.user_from_request_headers`` the REST routes use —
   Bearer ``phk_`` wins; otherwise auth_adapter resolves the configured
   forwarded identity. Unconfigured/anonymous requests are denied.

SDK note: ``mcp`` 2.x moved transport_security to streamable_http_app().
"""

from __future__ import annotations

import base64
from collections.abc import Callable, Mapping
from typing import Any

from fastapi import HTTPException
from mcp.server.mcpserver import Context, MCPServer
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.types import ASGIApp

from app import hub, queries
from app.auth_adapter import InvalidIdentityToken, build_transport_security
from app.config import get_settings
from app.db import SessionLocal
from app.deps import user_from_request_headers
from app.models.bundle import IMPORT_ACTIONS, Bundle, BundleFile, BundleVersion
from app.models.user import User

# The MCP spec has no HTTP status codes; a tool that fails raises, and the
# client shows the message. These read as instructions to the agent on
# purpose — "sign in" is more actionable than "401".
_NO_IDENTITY = (
    "Not signed in to phi hub. Sign in through your configured identity proxy "
    "or present an app token as 'Authorization: Bearer phk_...'."
)


class HubToolError(Exception):
    """A tool-level failure with a message meant for the calling agent."""


def _headers(ctx: Context) -> Mapping[str, str]:
    return ctx.headers or {}


def _identity(db: Session, ctx: Context) -> User:
    try:
        user = user_from_request_headers(db, _headers(ctx))
    except InvalidIdentityToken as exc:
        # A Bearer was present but didn't verify. Distinguish it from "no
        # credential at all" — the fixes are completely different (rotate
        # vs. sign in), and an agent that can't tell them apart will retry
        # the wrong one forever.
        raise HubToolError(
            f"The credential on this request failed verification ({exc}). "
            "Re-run `/hub login`, or mint a fresh token at "
            f"{get_settings().public_base_url}/me."
        ) from exc
    if user is None:
        raise HubToolError(_NO_IDENTITY)
    return user


def _bundle(db: Session, ref: str) -> tuple[Bundle, User]:
    owner, slug = _split_ref(ref)
    found = queries.find_bundle(db, owner, slug)
    if found is None:
        raise HubToolError(
            f"No bundle '{owner}/{slug}'. Use hub_search to find the right reference."
        )
    return found


def _split_ref(ref: str) -> tuple[str, str]:
    cleaned = (ref or "").strip().strip("/").split("@", 1)[0]
    parts = [p for p in cleaned.split("/") if p]
    if len(parts) != 2:
        raise HubToolError(f"Bundle references look like 'owner/slug' (got {ref!r}).")
    return parts[0], parts[1]


# --------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------


def hub_whoami(ctx: Context) -> dict[str, Any]:
    """Who the hub thinks you are, and what your profile holds.

    Call this first when a hub operation fails unexpectedly — it tells you
    whether the request carried a usable identity at all. A profile
    is created automatically on first contact; there is no signup step.
    """
    with SessionLocal() as db:
        user = _identity(db, ctx)
        profile = queries.profile_out(db, user)
        return {
            "signed_in": True,
            "handle": user.handle,
            "email": user.email,
            "display_name": profile.display_name,
            "bundles_published": profile.bundle_count,
            "bundles_installed": profile.import_count,
            "hub_url": get_settings().public_base_url,
        }


def hub_search(
    ctx: Context,
    query: str | None = None,
    kind: str | None = None,
    owner: str | None = None,
    org_only: bool = False,
) -> dict[str, Any]:
    """Search the hub for shareable phi (pi) config bundles.

    Every bundle in the org is visible to every signed-in user. Matches
    are substring, case-insensitive, over slug / title / description.

    Args:
        query: Free text, e.g. "routing" or "postgres review".
        kind: Filter by primary subtype — one of skill, extension, prompt,
            theme, instructions, settings, keybindings, mixed.
        owner: Filter to one publisher's handle (the ``owner`` in ``owner/slug``).
        org_only: Only bundles published under an ORG profile (e.g.
            ``team/...``) rather than an individual's. These are the
            curated marketplace entries: a named set of publishers gates
            what ships under the org handle, and phi auto-updates them by
            default. Prefer them when the user asks for "the standard" or
            "the official" anything.

    Returns the matching bundles newest-updated first, each with its
    ``ref`` (``owner/slug``) — the identifier every other hub tool takes.
    """
    with SessionLocal() as db:
        _identity(db, ctx)
        bundles, total = queries.search_bundles(
            db,
            q=query,
            kind=kind,
            owner=owner,
            org_only=org_only,
            sort=queries.SORT_UPDATED,
            limit=50,
        )
        return {
            "total": total,
            "returned": len(bundles),
            "bundles": [queries.as_tool_dict(b) for b in bundles],
        }


def hub_get_bundle(ctx: Context, ref: str) -> dict[str, Any]:
    """Full detail for one bundle, plus ready-to-run import instructions.

    Args:
        ref: The bundle reference, ``owner/slug`` (a trailing ``@v3`` is
            accepted and ignored — pass the version to hub_get_bundle_files).

    The ``import_instructions`` block is the point of this tool. It gives
    you two paths:

    - ``phi.install`` — if the user runs phi, that single slash-command is
      the whole job; the hub extension ships in every phi binary. It
      TRACKS THE LATEST version, which is what you almost always want:
      ``/hub update`` and phi's throttled startup check pull new versions
      as the publisher ships them. ``phi.pin`` is the same install frozen
      at the version shown — offer it only when the user explicitly wants
      the bundle to stop moving.
    - ``any_agent.steps`` — for any other agent: pull the bytes with
      hub_get_bundle_files, write them under the isolated store at
      ``~/.pi/agent/hub/bundles/<owner>/<slug>/v<N>/``, and link the bundle
      by appending that ONE absolute path to the ``packages`` array in
      ``~/.pi/agent/settings.json``. Never copy files into the user's own
      ``skills/`` / ``extensions/`` directories — the single-entry linkage
      is what makes disabling a bundle one reversible edit with no residue.
    """
    with SessionLocal() as db:
        viewer = _identity(db, ctx)
        bundle, owner = _bundle(db, ref)
        detail = queries.bundle_detail(db, bundle, owner, viewer=viewer)
        return queries.as_tool_dict(detail)


def hub_get_bundle_files(ctx: Context, ref: str, version: str = "latest") -> dict[str, Any]:
    """Every file in a bundle version, base64-encoded — enough to install it.

    Args:
        ref: ``owner/slug``.
        version: A version number as a string, or ``"latest"`` (default).

    Decode each ``content_b64`` and write it to
    ``~/.pi/agent/hub/bundles/<owner>/<slug>/v<version>/<path>``, creating
    parent directories as needed. Then add that directory's absolute path
    to the ``packages`` array in ``~/.pi/agent/settings.json`` and call
    hub_record_import so the author sees the install.

    Leaving ``version`` at ``"latest"`` is the normal case and means the
    user is TRACKING latest: record that fact locally so a later
    hub_check_updates knows the bundle may be moved forward. Passing an
    explicit number is a PIN — the user wants that exact version and it
    must not be silently upgraded afterwards.
    """
    with SessionLocal() as db:
        _identity(db, ctx)
        bundle, owner = _bundle(db, ref)
        if not bundle.latest_version:
            raise HubToolError(f"'{owner.handle}/{bundle.slug}' has no published versions yet.")
        try:
            resolved = queries.resolve_version(db, bundle, version)
        except HTTPException as exc:
            # The shared resolver speaks HTTP; MCP doesn't. Narrow on
            # HTTPException specifically so a genuine bug still surfaces as
            # a traceback instead of a polite "no such version".
            raise HubToolError(
                f"No version {version!r} of '{owner.handle}/{bundle.slug}'. "
                f"Published versions run 1..{bundle.latest_version}."
            ) from exc

        rows = (
            db.execute(
                select(BundleFile)
                .where(BundleFile.version_id == resolved.id)
                .order_by(BundleFile.path)
            )
            .scalars()
            .all()
        )
        return {
            "ref": f"{owner.handle}/{bundle.slug}",
            "version": resolved.version,
            "store_path": hub.store_path(owner.handle, bundle.slug, resolved.version),
            "settings_path": hub.SETTINGS_PATH,
            # The package's own `pi` manifest, so a materializing client can
            # write package.json back out rather than inventing one.
            "pi_manifest": resolved.pi_manifest,
            "files": [
                {
                    "path": f.path,
                    "size": f.size,
                    "is_text": f.is_text,
                    "content_b64": base64.b64encode(f.content).decode("ascii"),
                }
                for f in rows
            ],
        }


def hub_publish_bundle(
    ctx: Context,
    slug: str,
    title: str,
    description: str,
    kind: str,
    files: list[dict[str, str]],
    tags: list[str] | None = None,
    changelog: str | None = None,
) -> dict[str, Any]:
    """Publish a bundle under the caller's profile — creating it if new.

    Args:
        slug: 2-64 chars, lowercase letters / digits / dashes, starting
            alphanumeric. Unique per publisher.
        title: Short human name.
        description: What it does and when to reach for it.
        kind: skill | extension | prompt | theme | instructions | settings |
            keybindings | mixed.
        files: ``[{"path": "skills/foo/SKILL.md", "content_b64": "..."}]``.
            Paths are bundle-relative POSIX paths under the conventional pi
            directories (skills/, extensions/, prompts/, themes/) or the
            hub-only instructions/ and settings/.
        tags: Optional browse labels.
        changelog: What changed in this version.

    Publishing an existing slug adds version N+1; content is immutable per
    version. Everyone already tracking the bundle picks that version up at
    their next update check, so a publish is a release — put something
    useful in ``changelog``. Validation is all-or-nothing and refuses
    credential-shaped files outright (a stray ``models.json`` outside the
    canonical ``models/models.json`` path, ``.env*``, anything containing an
    API key or a literal provider credential), naming the offending path —
    nothing is stored when it trips.

    This tool always publishes under the CALLER's own handle. Publishing
    under an org profile (``team/...``) requires being one of that org's
    publishers and goes through ``POST /api/bundles`` with an ``owner``
    field; point the user at the web UI for that.
    """
    if kind not in hub.BUNDLE_KIND_SET:
        raise HubToolError(
            f"kind must be one of {', '.join(sorted(hub.BUNDLE_KIND_SET))} (got {kind!r})."
        )

    with SessionLocal() as db:
        user = _identity(db, ctx)
        try:
            hub.validate_slug(slug)
            decoded = hub.decode_files(list(files or []))
        except hub.PublishRejected as exc:
            raise HubToolError(
                f"Refused: {exc.message}" + (f" (file: {exc.path})" if exc.path else "")
            ) from exc

        bundle = db.execute(
            select(Bundle).where(
                Bundle.owner_id == user.id,
                Bundle.slug == slug,
                Bundle.deleted_at.is_(None),
            )
        ).scalar_one_or_none()

        created = bundle is None
        if bundle is None:
            bundle = Bundle(
                owner_id=user.id,
                slug=slug,
                title=title.strip(),
                description=(description or "").strip(),
                kind=kind,
                tags=[t.strip().lower() for t in (tags or []) if t and t.strip()],
            )
            db.add(bundle)
            try:
                db.flush()
            except IntegrityError as exc:
                db.rollback()
                raise HubToolError(f"You already publish a bundle called '{slug}'.") from exc
        else:
            bundle.title = title.strip() or bundle.title
            if description:
                bundle.description = description.strip()
            bundle.kind = kind
            if tags is not None:
                bundle.tags = [t.strip().lower() for t in tags if t and t.strip()]

        next_version = bundle.latest_version + 1
        version = BundleVersion(
            bundle_id=bundle.id,
            version=next_version,
            changelog=(changelog or "").strip(),
            file_count=len(decoded),
            total_bytes=sum(f.size for f in decoded),
            created_by_id=user.id,
        )
        db.add(version)
        db.flush()
        for f in decoded:
            db.add(
                BundleFile(
                    version_id=version.id,
                    path=f.path,
                    content=f.content,
                    size=f.size,
                    is_text=f.is_text,
                )
            )
        bundle.latest_version = next_version
        bundle.updated_at = func.now()
        db.commit()
        db.refresh(bundle)

        base_url = get_settings().public_base_url
        return {
            "ref": f"{user.handle}/{bundle.slug}",
            "created": created,
            "version": next_version,
            "file_count": len(decoded),
            "url": f"{base_url}/b/{user.handle}/{bundle.slug}",
            "share_with": f"/hub import {user.handle}/{bundle.slug}",
        }


def hub_record_import(
    ctx: Context, ref: str, action: str, version: int | None = None
) -> dict[str, Any]:
    """Record what you just did with a bundle, so its install count is real.

    Args:
        ref: ``owner/slug``.
        action: imported | enabled | disabled | removed | applied.
        version: The version acted on, when known.

    The hub folds this stream per (bundle, profile) and counts the ones
    whose latest action is ``imported`` or ``enabled`` — so recording a
    ``disabled`` genuinely takes you back out of the count. Call it after a
    successful install, and again when the user turns a bundle off.
    """
    if action not in IMPORT_ACTIONS:
        raise HubToolError(f"action must be one of {', '.join(IMPORT_ACTIONS)} (got {action!r}).")
    with SessionLocal() as db:
        user = _identity(db, ctx)
        bundle, owner = _bundle(db, ref)
        queries.record_event(db, bundle=bundle, user=user, action=action, version=version)
        return {
            "ref": f"{owner.handle}/{bundle.slug}",
            "action": action,
            "version": version,
            "install_count": queries.install_count(db, bundle.id),
        }


def hub_check_updates(ctx: Context, installed: list[dict[str, Any]]) -> dict[str, Any]:
    """Ask, in one call, which of the user's installed bundles have moved on.

    Args:
        installed: Everything the user currently has, as
            ``[{"ref": "team/routing", "version": 3}, ...]``. ``ref`` is
            ``owner/slug``; ``version`` is the version number materialized
            locally. In phi that list is ``~/.pi/agent/hub/state.json``.

    Returns ``{"updates": [...], "missing": [...]}``:

    - ``updates`` — bundles with a newer published version, each carrying
      ``latest_version``, ``title``, ``kind``, the new version's
      ``changelog``, and ``is_org``. A bundle the user is already current
      on appears in NEITHER list, so an empty ``updates`` means "nothing to
      do" and you should say exactly that rather than re-checking.
    - ``missing`` — refs that no longer resolve (the publisher deleted the
      bundle). Tell the user to remove them; they will never come back.

    Only bundles tracking LATEST should be offered an update. A bundle the
    user pinned with ``/hub import <ref>@vN`` is pinned on purpose — it may
    well show up here, and updating it anyway would undo a deliberate
    choice. In phi, ``track`` in ``hub/state.json`` says which is which.

    ``is_org`` matters for how pushy to be: bundles under an org profile
    are gated by that org's publishers, and phi's default
    ``hubAutoUpdate: "org"`` applies them without asking. Personal bundles
    are one individual's config — surface those, let the user decide.

    Cheap enough to call before advising on hub state, and far better than
    calling hub_get_bundle once per installed bundle.
    """
    entries: list[tuple[str, int]] = []
    for raw in installed or []:
        if not isinstance(raw, dict):
            raise HubToolError(
                'Each installed entry must be an object like {"ref": "owner/slug", "version": 3}.'
            )
        ref = str(raw.get("ref") or "").strip()
        if not ref:
            raise HubToolError('Each installed entry needs a "ref" of the form "owner/slug".')
        try:
            version = int(raw.get("version") or 0)
        except (TypeError, ValueError) as exc:
            raise HubToolError(
                f"version must be a whole number (got {raw.get('version')!r})."
            ) from exc
        entries.append((ref, max(version, 0)))

    with SessionLocal() as db:
        _identity(db, ctx)
        return queries.as_tool_dict(queries.check_updates(db, entries))


# Registered onto every MCPServer instance the factory builds.
_TOOLS: tuple[Callable[..., Any], ...] = (
    hub_whoami,
    hub_search,
    hub_get_bundle,
    hub_get_bundle_files,
    hub_publish_bundle,
    hub_record_import,
    hub_check_updates,
)


def build_mcp_app() -> ASGIApp:
    """Build a fresh MCP Streamable-HTTP ASGI app with the hub's tools.

    ``streamable_http_path="/"`` because ``main.py`` mounts the result
    under ``/mcp`` — mounting a sub-app that also owns ``/mcp`` would
    double the path. The externally visible endpoint stays a clean
    ``/mcp``, which is what the manifest declares and what the catalog's
    "Connect in Claude" link points at.
    """
    server = MCPServer(
        "phi-hub",
        instructions=(
            "phi hub — a registry of shareable phi (pi) agent config bundles: "
            "skills, extensions, prompts, themes, global instructions, settings "
            "fragments and provider-catalog (models) bundles. Use hub_search to find one "
            "(org_only=true for the curated "
            "marketplace, published under an org handle like `team/...` and gated by "
            "that org's named publishers), hub_get_bundle for its import instructions, "
            "hub_get_bundle_files to install it, hub_check_updates to see what the user "
            "already holds that has moved on, and hub_publish_bundle to share your own. "
            "Installs track the LATEST version by default (`/hub install <ref>`); a "
            "trailing `@vN` pins instead, and a pinned bundle must not be updated "
            "behind the user's back. Imports always land in an isolated store and are "
            "linked with a single packages entry — never merged into the user's own "
            "configuration."
        ),
    )
    for tool in _TOOLS:
        server.tool()(tool)
    return server.streamable_http_app(
        streamable_http_path="/",
        transport_security=build_transport_security(get_settings()),
    )
