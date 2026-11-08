"""FastAPI app factory."""

from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import orgs
from app.config import get_settings
from app.db import SessionLocal
from app.mcp_server import build_mcp_app
from app.routers import auth as auth_router
from app.routers import bundles as bundles_router
from app.routers import device as device_router
from app.routers import health as health_router
from app.routers import orgs as orgs_router
from app.routers import profiles as profiles_router
from app.routers import updates as updates_router


class SPAStaticFiles(StaticFiles):
    """StaticFiles that falls back to index.html on 404 so client-side
    routes (anything react-router renders below `/`) survive a hard
    reload or pasted URL. Without this, hitting a non-root SPA path
    returns {"detail":"Not Found"} from FastAPI before the router gets
    a chance to handle it.

    Two cases deliberately do NOT fall back, so they keep their honest 404:

    1. ``api/...`` — anything under the API prefix. A typo'd endpoint
       should surface as a JSON 404, not silently serve HTML; otherwise
       a fetch() call gets a parse error instead of a clear "no such
       endpoint" signal.
    2. Paths whose final segment looks like a file (contains a dot —
       ``foo.js``, ``logo.png``, ``manifest.webmanifest``, …). A missing
       asset should stay missing so build / deploy regressions are
       visible, not papered over with the SPA shell.
    """

    async def get_response(self, path, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404:
                raise
            # Don't paper over API 404s or missing-asset 404s. The path
            # passed here is relative to the mount (no leading slash).
            if path.startswith("api/") or "." in path.rsplit("/", 1)[-1]:
                raise
            return await super().get_response("index.html", scope)


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=level.upper(),
        stream=sys.stdout,
        format="%(message)s",
    )
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )


def _seed_marketplace() -> None:
    """Create the configured org profile + its initial publishers, once.

    Runs at startup rather than in a migration (see
    ``alembic/versions/0003_marketplace.py`` for the reasoning) and is
    idempotent, so every boot and every worker can safely call it.

    Failures are logged, never raised. The marketplace is one page of a
    working registry: if the seed cannot run — schema not migrated yet on
    a half-finished deploy, DB briefly unreachable — the right outcome is
    a degraded marketplace and a loud log line, not an app that refuses to
    serve ``/api/healthz``. The next boot retries.
    """
    log = structlog.get_logger("app")
    try:
        with SessionLocal() as db:
            orgs.ensure_seed(db)
    except Exception as exc:  # noqa: BLE001 — startup must not be fatal here
        log.error("marketplace_seed_failed", error=str(exc))


@asynccontextmanager
async def _lifespan(app: FastAPI):
    log = structlog.get_logger("app")
    settings = get_settings()
    log.info("startup", environment=settings.environment, app_env=settings.app_env)
    if (
        settings.org_handle
        and settings.org_display_name
        and settings.org_email
        and settings.initial_publisher_emails
    ):
        _seed_marketplace()
    # A mounted sub-app's lifespan does NOT run under the parent FastAPI
    # app. Without this nesting, FastMCP's session manager never starts
    # its task group and EVERY /mcp request 500s with "Task group is not
    # initialized". Pass the SUB-APP (the app that owns the router), not
    # the parent.
    sub = app.state.mcp_app
    async with sub.router.lifespan_context(sub):
        yield
    log.info("shutdown")


# Self-contained HTML card the catalog's anonymous Playwright screenshotter
# fetches. Pure HTML + CSS — DO NOT hit ``/api/*`` from here (the
# screenshotter has no identity headers, the response would be a 401 /
# login wall, and the catalog tile ends up blank).
_PREVIEW_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>phi hub — share your phi setup</title>
  <style>
    :root {
      --ex-red: #3056a4;
      --ex-orange-mid: #4279bb;
      --ex-orange: #29a4a0;
      --ex-neutral-900: #161922;
      --ex-neutral-500: #525866;
      --ex-neutral-200: #E2E4E9;
      --ex-neutral-100: #F6F8FA;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: 'Inter', system-ui, -apple-system, 'Helvetica Neue', sans-serif;
      background: var(--ex-neutral-100);
      color: var(--ex-neutral-900);
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 40px;
    }
    .card {
      width: 100%;
      max-width: 720px;
      background: #fff;
      border: 1px solid var(--ex-neutral-200);
      border-radius: 12px;
      padding: 44px 48px 40px;
      position: relative;
      overflow: hidden;
      box-shadow: 0 1px 3px rgba(0, 0, 0, 0.06);
    }
    .card::before {
      content: "";
      position: absolute;
      top: 0; left: 0; right: 0;
      height: 4px;
      background: linear-gradient(45deg, var(--ex-red), var(--ex-orange-mid) 50%, var(--ex-orange));
    }
    .eyebrow {
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--ex-neutral-500);
    }
    h1 {
      font-size: 40px;
      margin: 10px 0 12px;
      letter-spacing: -0.03em;
      background: linear-gradient(45deg, var(--ex-red), var(--ex-orange-mid) 50%, var(--ex-orange));
      -webkit-background-clip: text;
      background-clip: text;
      -webkit-text-fill-color: transparent;
      display: inline-block;
    }
    p.lede {
      font-size: 17px;
      line-height: 1.5;
      margin: 0 0 28px;
      color: var(--ex-neutral-500);
      max-width: 56ch;
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 12px;
      margin-bottom: 26px;
    }
    .tile {
      border: 1px solid var(--ex-neutral-200);
      border-radius: 8px;
      padding: 14px 16px;
      background: var(--ex-neutral-100);
    }
    .tile-label {
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--ex-neutral-500);
      font-weight: 700;
    }
    .tile-value { font-size: 15px; font-weight: 700; margin-top: 4px; }
    code {
      display: inline-block;
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: 14px;
      background: var(--ex-neutral-900);
      color: #fff;
      border-radius: 6px;
      padding: 10px 14px;
    }
  </style>
</head>
<body>
  <div class="card">
    <div class="eyebrow">Self-hostable registry</div>
    <h1>phi hub</h1>
    <p class="lede">
      Publish, browse and import phi agent config &mdash; skills, extensions, prompts,
      themes, global instructions and settings fragments &mdash; for your team.
      Imports land in an isolated store and unlink cleanly.
    </p>
    <div class="grid">
      <div class="tile">
        <div class="tile-label">Browse</div>
        <div class="tile-value">Every bundle, org-wide</div>
      </div>
      <div class="tile">
        <div class="tile-label">Publish</div>
        <div class="tile-value">Versioned, secret-guarded</div>
      </div>
      <div class="tile">
        <div class="tile-label">Import</div>
        <div class="tile-value">One reversible link</div>
      </div>
    </div>
    <code>/hub import sample/routing-setup</code>
  </div>
</body>
</html>
"""


def create_app() -> FastAPI:
    settings = get_settings()
    _configure_logging(settings.log_level)

    app = FastAPI(
        title="phi hub",
        version="0.1.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        lifespan=_lifespan,
    )

    # In production the frontend is served from the same origin (mounted at /),
    # so CORS isn't needed and exposing it would only widen the surface.
    if not settings.is_prod:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins or ["http://localhost:5173"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    app.include_router(auth_router.router, prefix="/api")
    app.include_router(health_router.router, prefix="/api")
    app.include_router(device_router.router, prefix="/api")
    app.include_router(profiles_router.router, prefix="/api")
    app.include_router(bundles_router.router, prefix="/api")
    app.include_router(orgs_router.router, prefix="/api")
    app.include_router(updates_router.router, prefix="/api")

    # Public preview target: static HTML only, no private bundle API calls.
    # Mount before the SPA fallback.
    @app.get("/preview", response_class=HTMLResponse)
    async def preview() -> str:
        return _PREVIEW_HTML

    # MCP surface. Mounted BEFORE the SPA static mount — the SPA's 404
    # fallback would otherwise swallow /mcp requests. Stashed on app.state
    # so ``_lifespan`` can run the sub-app's lifespan (see the comment
    # there; without it every /mcp request 500s).
    mcp_app = build_mcp_app()
    app.state.mcp_app = mcp_app
    app.mount("/mcp", mcp_app)

    if settings.is_prod:
        # backend/src/app/main.py → ../../../frontend/dist
        frontend_dist = Path(__file__).resolve().parents[3] / "frontend" / "dist"
        if frontend_dist.is_dir():
            app.mount("/", SPAStaticFiles(directory=str(frontend_dist), html=True), name="static")

    return app


app = create_app()
