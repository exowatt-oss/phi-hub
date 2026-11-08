"""Hub domain logic: publish validation, install counting, import instructions.

Everything here is surface-agnostic — the REST routers and the MCP tools
both call into this module so the two channels can never drift on what a
valid bundle is or what "installed" means. See ``README.md``.
"""

from __future__ import annotations

import base64
import binascii
import fnmatch
import gzip
import io
import json
import posixpath
import re
import tarfile
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Select, Subquery, func, select

from app.models.bundle import BUNDLE_KINDS, INSTALLED_ACTIONS, Bundle, ImportEvent

# Set form for O(1) validation in the MCP tools (the REST layer gets this
# for free from its Literal-typed pydantic schema).
BUNDLE_KIND_SET = frozenset(BUNDLE_KINDS)

# --------------------------------------------------------------------------
# Publish limits (contract → "Server-side validation on publish")
# --------------------------------------------------------------------------

MAX_FILES = 200
MAX_FILE_BYTES = 512 * 1024  # 512 KB per file
MAX_VERSION_BYTES = 5 * 1024 * 1024  # 5 MB per version, decoded

# Slug shape: `[a-z0-9][a-z0-9-]{1,63}` — 2..64 chars, must start
# alphanumeric. Anchored so a slug with a newline in it can't sneak past.
SLUG_RE = re.compile(r"\A[a-z0-9][a-z0-9-]{1,63}\Z")

# The ONE place a `models.json` basename is allowed: a `models` bundle's
# canonical provider catalog at exactly `models/models.json`. Anywhere else
# a `models.json` is refused by the secret guard as before — the exemption is
# deliberately a single hard-coded path, not "any models.json under models/",
# so a bundle cannot smuggle a second provider file (`models/prod.models.json`,
# `models/nested/models.json`) past the guard. See `validate_models_json`.
CANONICAL_MODELS_PATH = "models/models.json"

# --------------------------------------------------------------------------
# Secret guard
# --------------------------------------------------------------------------

# Filenames that are refused outright regardless of content — these are
# where pi/phi keeps provider credentials, and there is no legitimate
# reason to publish one. Matched with fnmatch against the BASENAME, so
# `settings/models.json` is caught as surely as a top-level `models.json`.
#
# `models.json` stays on this list: a raw `models.json` is where pi keeps
# LIVE provider credentials, so the basename is refused everywhere EXCEPT
# the single canonical `models/models.json` path, which `check_secret_guard`
# routes to the structural `validate_models_json` check instead. There is no
# other legitimate `models.json` in a shareable bundle.
SECRET_FILENAME_PATTERNS = (
    "models.json",
    "auth.json",
    "credentials*",
    ".env*",
    "hub.json",
    # MCP server configs carry live server credentials in `env` blocks and
    # `headers.Authorization` — a leaked mcp.json is a full set of keys for
    # every bridged server. A bundle that wants to ship one publishes an
    # `mcp.json.example` (a different basename) using $ENV references; the
    # bearer-token content pattern below keeps literal tokens out of that.
    "mcp.json",
)

# Content shapes that look like a live credential. Scanned over the raw
# bytes (latin-1 decoded, so binary payloads are covered too — a base64
# blob that happens to contain `ghp_` is a false positive we accept: the
# cost of a rejected publish is a rename, the cost of a leaked token is
# a rotation across the org).
SECRET_CONTENT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("anthropic-api-key", re.compile(r"sk-ant-")),
    ("openai-project-key", re.compile(r"sk-proj-")),
    ("aws-access-key-id", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("private-key-block", re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")),
    ("slack-token", re.compile(r"xox[bap]-")),
    ("github-pat", re.compile(r"ghp_")),
    ("phi-hub-token", re.compile(r"phk_")),
    ("grafana-service-account-token", re.compile(r"glsa_")),
    # A literal bearer token wired into a JSON config — the shape an
    # mcp.json or an extension's settings fragment leaks credentials in.
    # Placeholders are deliberately allowed through so a bundle can still
    # document the header: anything starting with `$` ($TOKEN, ${TOKEN}),
    # `<` (<token>) or `{` ({{ env.TOKEN }}) is an env reference, not a
    # secret. A real token is 8+ characters with no space or quote in it.
    (
        "bearer-token-in-json",
        re.compile(r'"[Aa]uthorization"\s*:\s*"\s*[Bb]earer\s+(?![$<{])[^"\s]{8,}'),
    ),
)


class PublishRejected(Exception):
    """A bundle payload failed validation.

    Carries the offending ``path`` so the 422 can name the exact file the
    publisher has to fix — the contract's explicit requirement. Routers
    turn this into ``HTTPException(422, detail={error, path, message})``;
    MCP tools turn it into a tool error with the same text.
    """

    def __init__(self, error: str, message: str, path: str | None = None) -> None:
        super().__init__(message)
        self.error = error
        self.message = message
        self.path = path

    def as_detail(self) -> dict[str, str | None]:
        return {"error": self.error, "path": self.path, "message": self.message}


@dataclass(frozen=True, slots=True)
class DecodedFile:
    """One validated file, ready to persist."""

    path: str
    content: bytes
    is_text: bool

    @property
    def size(self) -> int:
        return len(self.content)


def validate_slug(slug: str) -> str:
    """Return the slug if it matches the contract's shape, else raise."""
    if not SLUG_RE.match(slug or ""):
        raise PublishRejected(
            "invalid_slug",
            "Slug must be 2-64 characters of lowercase letters, digits and dashes, "
            "starting with a letter or digit.",
        )
    return slug


def validate_path(path: str) -> str:
    """Validate one bundle-relative path, or raise :class:`PublishRejected`.

    Enforces the contract's four rules — relative, normalized, no ``..``,
    no ``.git/`` — plus the implied ones that make "relative" meaningful
    on every platform the phi client runs on: no backslash separators
    (a Windows-style ``..\\..\\x`` would otherwise pass a POSIX check),
    no drive letters, no NUL bytes, no empty segments.
    """

    def reject(message: str) -> None:
        raise PublishRejected("invalid_path", message, path=path)

    if not path or not isinstance(path, str):
        reject("Every file needs a non-empty path.")
    if "\x00" in path:
        reject("Path contains a NUL byte.")
    if "\\" in path:
        reject("Use forward slashes in bundle paths; backslashes are not allowed.")
    if path.startswith("/"):
        reject("Paths must be relative — no leading '/'.")
    if re.match(r"\A[A-Za-z]:", path):
        reject("Paths must be relative — no drive letter.")
    if posixpath.normpath(path) != path:
        reject(
            "Path is not normalized. Write it exactly as it should land on disk "
            f"(expected {posixpath.normpath(path)!r})."
        )

    segments = path.split("/")
    if any(seg in ("", ".", "..") for seg in segments):
        reject("Path may not contain empty, '.' or '..' segments.")
    if any(seg == ".git" for seg in segments):
        reject("'.git' paths are not publishable.")
    if len(path) > 400:
        reject("Path is longer than 400 characters.")
    return path


def check_secret_guard(path: str, content: bytes) -> None:
    """Refuse credential-shaped files. Raises :class:`PublishRejected`.

    Filename check first (cheap, and the more common case), then a
    content scan. The caller must run this BEFORE persisting anything —
    the contract is explicit that a rejected payload is never stored.

    The one basename that is normally refused but can be legitimate is
    ``models.json``: at exactly ``models/models.json`` it is a ``models``
    bundle's canonical provider catalog, which is safe to share as long as
    it carries no literal credentials. That path is routed to
    :func:`validate_models_json` — a structural check that both enforces the
    catalog shape and runs the same credential scan at every depth — instead
    of the blanket filename refusal. Every OTHER ``models.json`` (top-level,
    buried in a subtree, a second one under ``models/``) still trips the
    filename guard.
    """
    basename = posixpath.basename(path)

    if basename == "models.json":
        if path == CANONICAL_MODELS_PATH:
            validate_models_json(path, content)
            return
        raise PublishRejected(
            "secret_filename",
            "A 'models.json' may only be published as the canonical "
            f"'{CANONICAL_MODELS_PATH}' — that path is validated as a credential-free "
            "provider catalog. Every other 'models.json' is refused because a raw one "
            "holds live provider keys.",
            path=path,
        )

    for pattern in SECRET_FILENAME_PATTERNS:
        if fnmatch.fnmatch(basename, pattern):
            raise PublishRejected(
                "secret_filename",
                f"'{basename}' is on the secret-guard filename list ({pattern}). "
                "Credential files are never published to the hub.",
                path=path,
            )

    # latin-1 never raises on arbitrary bytes, so one decode covers text
    # and binary alike without a try/except around every file.
    haystack = content.decode("latin-1")
    for label, pattern in SECRET_CONTENT_PATTERNS:
        if pattern.search(haystack):
            raise PublishRejected(
                "secret_content",
                f"Content looks like a credential ({label}). Remove it and republish; "
                "the hub never stores a payload that trips the secret guard.",
                path=path,
            )


def _looks_like_env_or_command(value: str) -> bool:
    """True if a string is a pi value-resolution reference, not a literal secret.

    pi's ``models.json`` resolves ``apiKey`` / ``headers`` values three ways
    (see phi ``docs/models.md`` → "value resolution"): a leading ``!`` runs a
    shell command, ``$VAR`` / ``${VAR}`` interpolates an environment variable
    (anywhere in the string), and ``$$`` / ``$!`` are literal-escape prefixes.
    All of those are safe to publish — they carry no secret, only a reference
    to one resolved on the importer's own machine. A ``<...>`` /
    ``{{ ... }}`` placeholder is documentation, equally safe. We never execute
    the command; we only recognise the shape so we do not reject it as a
    literal.
    """
    stripped = value.strip()
    if not stripped:
        return True
    # `!command`, `$ENV`/`${ENV}`, `$$literal`/`$!literal` escapes, and any
    # value that interpolates an env var somewhere inside it.
    if stripped[0] in ("!", "$", "<", "{"):
        return True
    if re.search(r"\$\{?[A-Za-z_][A-Za-z0-9_]*\}?", stripped):
        return True
    return False


# Keys whose VALUE is credential-bearing wherever it appears in a provider
# catalog. `apiKey` is pi's own field; the header keys catch a literal token
# wired straight into a provider's request headers. Matched case-insensitively.
_MODELS_SECRET_KEYS = frozenset(
    {"apikey", "api_key", "authorization", "x-api-key", "bearer", "token", "secret"}
)


def _scan_models_value(path: str, key: str, value: str) -> None:
    """Reject a literal credential sitting under a credential-bearing key.

    Env/command references and placeholders pass; a bare literal that either
    matches a known key pattern or is simply a non-reference string under a
    secret-bearing key is refused. The key name is threaded into the message
    so the publisher knows which field to turn into a ``$ENV`` reference.
    """
    lowered = key.lower()
    # A known-shaped credential anywhere (sk-ant-, ghp_, a bearer literal, …)
    # is refused regardless of the key it hides under.
    for label, pattern in SECRET_CONTENT_PATTERNS:
        if pattern.search(value):
            raise PublishRejected(
                "secret_content",
                f"'{CANONICAL_MODELS_PATH}' has a value under '{key}' that looks like a "
                f"credential ({label}). Use an environment reference such as '$MY_API_KEY' "
                "or a '!command' so the secret is resolved on the importer's machine.",
                path=path,
            )
    if lowered in _MODELS_SECRET_KEYS and not _looks_like_env_or_command(value):
        raise PublishRejected(
            "secret_content",
            f"'{CANONICAL_MODELS_PATH}' carries a literal '{key}' value. Provider "
            "credentials must be an environment reference ('$MY_API_KEY', '${MY_API_KEY}') "
            "or a '!command' — never a literal, so the catalog is safe to share.",
            path=path,
        )


def _walk_models(path: str, node: Any) -> None:
    """Recurse the whole catalog, scanning every credential-bearing string.

    A provider's ``apiKey`` and a model's ``headers.Authorization`` sit at
    different depths, so the scan cannot assume a fixed shape — it visits
    every object key and list element and checks any string value whose key
    is credential-bearing (plus every string for a known credential shape).
    """
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(value, str):
                _scan_models_value(path, str(key), value)
            else:
                _walk_models(path, value)
    elif isinstance(node, list):
        for item in node:
            _walk_models(path, item)


def validate_models_json(path: str, content: bytes) -> None:
    """Validate the canonical ``models/models.json`` provider catalog.

    This is what lets the ONE ``models.json`` basename through the secret
    guard (contract → "Model bundles"). It is strict on purpose — the whole
    reason a raw ``models.json`` is otherwise refused is that it is where
    live provider keys live, so a shareable one has to prove it holds none:

    - Parses as a JSON **object**; anything else is ``invalid_json``.
    - Has a ``providers`` **object** (pi's catalog shape); otherwise
      ``invalid_models_schema``.
    - Carries **no literal credential at any depth**: every ``apiKey`` /
      ``Authorization`` / ``Bearer`` / ``token`` value, and any string that
      matches a known credential pattern, must be an environment reference
      (``$ENV`` / ``${ENV}``) or a ``!command`` — which are recognised but
      never executed. A literal is ``secret_content``.

    Raises :class:`PublishRejected`; returns ``None`` when the catalog is a
    safe, shareable provider file.
    """
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PublishRejected(
            "invalid_json",
            f"'{CANONICAL_MODELS_PATH}' must be UTF-8 JSON ({exc}).",
            path=path,
        ) from exc
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PublishRejected(
            "invalid_json",
            f"'{CANONICAL_MODELS_PATH}' is not valid JSON ({exc}).",
            path=path,
        ) from exc

    if not isinstance(parsed, dict):
        raise PublishRejected(
            "invalid_json",
            f"'{CANONICAL_MODELS_PATH}' must be a JSON object, not a {type(parsed).__name__}.",
            path=path,
        )
    providers = parsed.get("providers")
    if not isinstance(providers, dict):
        raise PublishRejected(
            "invalid_models_schema",
            f"'{CANONICAL_MODELS_PATH}' must have a 'providers' object mapping provider "
            "ids to their config — that is the pi model-catalog shape.",
            path=path,
        )

    _walk_models(path, parsed)


def decode_files(raw_files: list[dict[str, Any]]) -> list[DecodedFile]:
    """Validate + base64-decode a publish payload.

    Order matters and is deliberate: the file-count cap is checked before
    anything is decoded (so a 10k-entry payload is rejected without
    allocating it), then each file is path-checked, decoded, size-checked,
    and secret-scanned, with the running total checked as we go. Nothing
    reaches the DB until every file has passed.
    """
    if not raw_files:
        raise PublishRejected("empty_version", "A version needs at least one file.")
    if len(raw_files) > MAX_FILES:
        raise PublishRejected(
            "too_many_files",
            f"A version may contain at most {MAX_FILES} files (got {len(raw_files)}).",
        )

    decoded: list[DecodedFile] = []
    seen: set[str] = set()
    total = 0

    for entry in raw_files:
        path = validate_path(str(entry.get("path") or ""))
        if path in seen:
            raise PublishRejected("duplicate_path", "Duplicate path in payload.", path=path)
        seen.add(path)

        try:
            # `validate=True` so stray characters are an error rather than
            # being silently dropped into a different payload than the
            # publisher meant to send.
            content = base64.b64decode(str(entry.get("content_b64") or ""), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise PublishRejected(
                "invalid_base64", f"content_b64 is not valid base64 ({exc}).", path=path
            ) from exc

        if len(content) > MAX_FILE_BYTES:
            raise PublishRejected(
                "file_too_large",
                f"File is {len(content)} bytes; the per-file cap is {MAX_FILE_BYTES}.",
                path=path,
            )
        total += len(content)
        if total > MAX_VERSION_BYTES:
            raise PublishRejected(
                "version_too_large",
                f"Version exceeds the {MAX_VERSION_BYTES}-byte decoded cap.",
                path=path,
            )

        check_secret_guard(path, content)

        try:
            content.decode("utf-8")
            is_text = True
        except UnicodeDecodeError:
            is_text = False

        decoded.append(DecodedFile(path=path, content=content, is_text=is_text))

    return decoded


def infer_kind(paths: list[str]) -> str:
    """Guess a bundle's primary subtype from its top-level directories.

    Used when a publisher doesn't state one. A single conventional
    directory wins; anything else is ``mixed``.
    """
    tops = {p.split("/", 1)[0] for p in paths if "/" in p}
    mapping = {
        "skills": "skill",
        "extensions": "extension",
        "prompts": "prompt",
        "themes": "theme",
        "instructions": "instructions",
        "settings": "settings",
        "models": "models",
    }
    known = {mapping[t] for t in tops if t in mapping}
    if len(known) == 1:
        return known.pop()
    return "mixed"


# --------------------------------------------------------------------------
# Install counting
# --------------------------------------------------------------------------


def install_count_subquery() -> Subquery:
    """``(bundle_id, installs)`` — distinct profiles currently holding a bundle.

    The contract defines the install count as "distinct profiles whose
    LATEST event is imported/enabled", which makes it a per-(bundle,user)
    last-write-wins fold rather than a running total: a ``disabled`` or
    ``removed`` event has to take the profile back out of the count. A
    window function gets that in one pass, so the browse page costs one
    extra join instead of one query per card.
    """
    ranked = select(
        ImportEvent.bundle_id.label("bundle_id"),
        ImportEvent.action.label("action"),
        func.row_number()
        .over(
            partition_by=(ImportEvent.bundle_id, ImportEvent.user_id),
            order_by=(ImportEvent.created_at.desc(), ImportEvent.id.desc()),
        )
        .label("rn"),
    ).subquery("ranked_events")
    return (
        select(ranked.c.bundle_id, func.count().label("installs"))
        .where(ranked.c.rn == 1, ranked.c.action.in_(INSTALLED_ACTIONS))
        .group_by(ranked.c.bundle_id)
        .subquery("install_counts")
    )


def apply_search(stmt: Select, q: str | None) -> Select:
    """Case-insensitive substring match across slug / title / description."""
    if not q:
        return stmt
    needle = f"%{q.strip().lower()}%"
    return stmt.where(
        func.lower(Bundle.slug).like(needle)
        | func.lower(Bundle.title).like(needle)
        | func.lower(Bundle.description).like(needle)
    )


# --------------------------------------------------------------------------
# Import instructions
# --------------------------------------------------------------------------

# Where the phi hub extension materializes an import. Isolated from the
# user's own resource dirs on purpose (contract → "Linkage rule").
STORE_TEMPLATE = "~/.pi/agent/hub/bundles/{owner}/{slug}/v{version}"
SETTINGS_PATH = "~/.pi/agent/settings.json"


def store_path(owner: str, slug: str, version: int) -> str:
    return STORE_TEMPLATE.format(owner=owner, slug=slug, version=version)


def import_instructions(*, base_url: str, owner: str, slug: str, version: int) -> dict[str, Any]:
    """Everything a caller needs to actually install the bundle.

    Two audiences, one payload:

    - **phi** already ships the ``hub`` extension, so it gets a single
      slash-command and nothing else to think about.
    - **Any other MCP-connected agent** (Claude Desktop, Cowork, a
      bespoke harness) gets the mechanical steps: pull the bytes from
      ``hub_get_bundle_files``, write them under the isolated store, and
      link the bundle with ONE absolute path in pi's ``packages`` array.
      That single-entry linkage is the contract's no-muddling invariant —
      disabling is removing that one line, with zero residue in the
      user's own ``skills/`` or ``extensions/``.
    """
    ref = f"{owner}/{slug}"
    path = store_path(owner, slug, version)
    files_url = f"{base_url}/api/bundles/{owner}/{slug}/versions/{version}/files"
    return {
        "ref": ref,
        "version": version,
        "store_path": path,
        "settings_path": SETTINGS_PATH,
        "phi": {
            "install": f"/hub install {ref}",
            "command": f"/hub import {ref}",
            "pin": f"/hub import {ref}@v{version}",
            "note": (
                "phi ships the hub extension built in — no install step. `/hub install` "
                f"(and a bare `/hub import`) tracks the LATEST version: `/hub update` and "
                "the throttled startup check pull new versions as the publisher ships "
                f"them. Adding `@v{version}` pins instead, and the bundle stops moving. "
                "Either way it materializes into an isolated store and links with one "
                f"entry in {SETTINGS_PATH}; undo with `/hub disable {ref}`."
            ),
            "track": "latest",
        },
        "any_agent": {
            "summary": (
                f"Materialize {ref} v{version} into pi's isolated hub store, then link it "
                "with a single packages entry."
            ),
            "steps": [
                f"Call hub_get_bundle_files(ref='{ref}', version='{version}') to get every "
                "file as {path, content_b64}.",
                f"Base64-decode each file and write it to {path}/<path>, creating parent "
                "directories as needed. Do NOT write into the user's own ~/.pi/agent/skills/, "
                "extensions/, prompts/ or themes/ — the store stays isolated.",
                f"Open {SETTINGS_PATH} and append the ABSOLUTE form of {path} to the "
                '"packages" array (create the array if it is missing). pi loads the '
                "bundle's resources namespaced from there.",
                f"Record the import so the author sees it: hub_record_import(ref='{ref}', "
                f"action='imported', version={version}).",
                "To disable later, remove that one packages entry — files and state stay "
                "put and re-adding the entry re-enables it.",
            ],
            "never": [
                "Never copy bundle files into the user's own resource directories.",
                f"Never merge a bundle's settings/ fragment into {SETTINGS_PATH} "
                "automatically — that needs an explicit `/hub apply-settings` with a "
                "backup the user can revert.",
            ],
        },
        "rest": {
            "files_url": files_url,
            "archive_url": (
                f"{base_url}/api/bundles/{owner}/{slug}/versions/{version}/archive.tgz"
            ),
            "curl": (f'curl -H "Authorization: Bearer $PHI_HUB_TOKEN" \\\n  "{files_url}"'),
        },
    }


def agent_prompt(owner: str, slug: str) -> str:
    """The copy-paste prompt for a non-phi agent that has the hub's MCP surface."""
    ref = f"{owner}/{slug}"
    return (
        f"Import the phi hub bundle {ref} for me. Use the phi hub MCP tools: call "
        f"hub_get_bundle('{ref}') for the metadata and instructions, then "
        f"hub_get_bundle_files('{ref}') for the contents. Write the decoded files under "
        f"~/.pi/agent/hub/bundles/{owner}/{slug}/v<version>/, add that absolute path to the "
        f'"packages" array in ~/.pi/agent/settings.json, and finally call '
        f"hub_record_import('{ref}', 'imported'). Do not copy anything into my own "
        f"skills/ or extensions/ directories."
    )


def import_snippets(*, base_url: str, owner: str, slug: str, version: int) -> dict[str, str]:
    """The copyable snippets the web UI's Import modal shows.

    ``phi_install`` is the primary one and the only one most people want:
    it tracks the latest version, which is what makes the marketplace's
    auto-update story work. ``phi_pin`` is the escape hatch for anyone who
    needs the bundle to stop moving.

    ``phi_command`` is the bare ``/hub import`` form. It behaves exactly
    like ``phi_install`` (contract → "Client semantics": a bare import is
    track-latest) and is kept because it is the string every existing
    client, doc and screenshot already says.
    """
    ref = f"{owner}/{slug}"
    files_url = f"{base_url}/api/bundles/{owner}/{slug}/versions/{version}/files"
    return {
        "phi_install": f"/hub install {ref}",
        "phi_command": f"/hub import {ref}",
        "phi_pin": f"/hub import {ref}@v{version}",
        "agent_prompt": agent_prompt(owner, slug),
        "curl": f'curl -H "Authorization: Bearer $PHI_HUB_TOKEN" \\\n  "{files_url}"',
    }


# --------------------------------------------------------------------------
# Archive
# --------------------------------------------------------------------------


def build_archive(
    *, owner: str, slug: str, version: int, files: list[tuple[str, bytes]], mtime: datetime
) -> bytes:
    """Pack a version into a deterministic ``tar.gz``.

    Everything lands under a single ``<owner>-<slug>-v<N>/`` directory so
    an unpack in the wrong cwd can't scatter files (the classic tar-bomb).
    Ownership, mode and mtime are pinned to the version's publish time so
    the same version always produces byte-identical output — a re-download
    is diff-clean and the endpoint is safe to cache.

    The gzip wrapper is applied by hand rather than via ``mode="w:gz"``
    because tarfile gives no way to pin the gzip header's own mtime, and
    that one field would otherwise make every response differ.
    """
    root = f"{owner}-{slug}-v{version}"
    stamp = int(mtime.timestamp())

    tar_buf = io.BytesIO()
    with tarfile.open(fileobj=tar_buf, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for path, content in sorted(files):
            info = tarfile.TarInfo(name=f"{root}/{path}")
            info.size = len(content)
            info.mtime = stamp
            info.mode = 0o644
            info.uid = info.gid = 0
            info.uname = info.gname = "root"
            tar.addfile(info, io.BytesIO(content))

    gz_buf = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=gz_buf, mtime=stamp) as gz:
        gz.write(tar_buf.getvalue())
    return gz_buf.getvalue()
