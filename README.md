# phi-hub

A self-hostable registry for versioned phi agent-configuration bundles (skills, extensions, themes and settings), with REST, MCP and a browser UI.

![Status: maintained](https://img.shields.io/badge/status-maintained-brightgreen) ![License: Apache--2.0](https://img.shields.io/badge/license-Apache--2.0-blue)

Created at [Exowatt](https://exowatt.com). This repository contains **platform code only**; Exowatt's production bundles, publisher roster, database, backups and private deployment configuration are not included.

## Status

Maintained. Authentication uses [`forwarded-auth`](https://github.com/exowatt-oss/forwarded-auth) behind one shared REST/MCP adapter. The dependency is pinned to a reviewed commit; **publish forwarded-auth first**. Never attach this public tree to Exowatt's production database.

## Install

Requires Python 3.12+, [uv](https://docs.astral.sh/uv/), Node.js/npm and an empty PostgreSQL 16 database. No Exowatt credentials or services are needed once the dependency is public. **CI installs forwarded-auth from GitHub; while that repo is private, CI needs read access to it.** A repository's default `GITHUB_TOKEN` does not grant access to a sibling private repository. If CI must run before publication, grant an approved GitHub App installation token or fine-grained PAT Contents: read access through a repository secret; never embed credentials in the dependency URL or lockfile.

```sh
cp .env.example .env
# Start a local, empty Postgres (or supply your own disposable DATABASE_URL).
docker compose -f docker-compose.dev.yml --profile db up -d postgres
(cd backend && uv sync && uv run alembic upgrade head)
(cd frontend && npm ci)
```

Start the backend and frontend in separate shells:

```sh
(cd backend && uv run uvicorn app.main:app --host 127.0.0.1 --port 8000)
(cd frontend && npm run dev -- --host 127.0.0.1)
```

Open `http://localhost:5173`; the Vite dev server proxies `/api` and `/mcp` to port 8000. The checked-in example uses a synthetic `dev@example.org` profile, and seeds **no organization and no bundles**. A fresh empty hub therefore has an empty marketplace. Use the UI to publish a synthetic bundle.

## Usage / Quickstart

Sign in locally using the development-mode profile from `.env`. Create a bundle on **My hub**, upload a small text file under its first version, then browse and install `dev/<slug>` through a phi client configured to use `http://localhost:5173` (for example, set `PHI_HUB_URL`). API documentation is at `http://localhost:8000/api/docs`. To run tests against the disposable database: `(cd backend && uv run pytest)` and `(cd frontend && npm test -- --maxWorkers=4)`. Run backend tests serially: they share and truncate one database, so parallel workers can race.

## Configuration and security

`AUTH_MODE=disabled` (the default without `.env`) **denies bundle/profile reads and writes without a previously issued `phk_` app token**, even with spoofed headers. Revoke previously issued tokens before switching to disabled mode if you need to deny all access. `/preview` is static HTML; device authorization start/poll are intentionally anonymous but reveal no bundle content. `AUTH_MODE=dev` maps to forwarded-auth's `none` mode, requires a **second explicit** `AUTH_ALLOW_INSECURE_DEV_MODE=true`, and is allowed only when **both** `ENVIRONMENT` and `APP_ENV` are one of `local`, `dev`, `development`, `test` (case-insensitive). Never use dev mode with sensitive data or on a network-accessible server.

For a proxy, set `AUTH_MODE=forwarded`, `AUTH_TRUST_FORWARDED_HEADERS=true`, `AUTH_ISSUER=https://login.example.org`, `AUTH_AUDIENCE=phi-hub`, and an **exact** comma-separated `AUTH_ALLOWED_DOMAINS` (e.g. `example.org`, not `*`). `AUTH_JWKS_URL` is optional if the issuer serves OIDC discovery. Signed RS256 bearer JWTs must pass issuer, audience, email-verified, signature and domain checks; when an email header is present it must match the signed claim. A header-only request is trusted **only** in this explicitly opted-in forwarded mode. The package also has an OIDC direct-bearer path: `AUTH_MODE=oidc`, same issuer/audience/domain settings, and **no** header-trust switches. Neither mode falls back from an invalid non-`phk_` bearer to header trust.

**Trust boundary:** the proxy must authenticate every identity-bearing request, strip *all inbound* identity headers before setting its own, and make the backend port unreachable directly. For a *separate* authenticated MCP edge, additionally opt into `AUTH_TRUST_MCP_HEADERS=true` and set `AUTH_MCP_EMAIL_HEADER`/`AUTH_MCP_NAME_HEADER`; those identity headers are accepted only on `/mcp`, never on REST or device approval. Both proxies must strip inbound MCP identity headers. The same domain allowlist and bearer verification policy applies. Set `AUTH_ALLOWED_HOSTS=hub.example.org` for the MCP Host/Origin DNS-rebinding guard: forwarded/OIDC mode rejects loopback hosts by default; `AUTH_ALLOW_LOOPBACK_HOSTS=true` is available in local/test environments only. Set `VITE_SIGN_IN_URL` at frontend build time and `AUTH_LOGOUT_URL` for your IdP. App-issued `phk_` Bearer tokens are verified by the hub first and may pass through the proxy; unknown or revoked tokens never become a forwarded identity. Header-only trust is safe **only** behind the authenticated, stripping, isolated proxy described above, not on a directly exposed backend.

For a named marketplace org, configure **all four** of `ORG_HANDLE`, `ORG_DISPLAY_NAME`, `ORG_EMAIL` (reserved address rejected if presented at sign-in), `ORG_INITIAL_PUBLISHERS`. Seeding is opt-in and additive: removing a configured publisher later does not revoke their grant; use the authenticated publisher API. `APP_BASE_URL`/`HUB_PUBLIC_URL` control outward-facing links; `DATABASE_URL` points to your **own empty or synthetic** Postgres. No export or migration includes production rows.

The browser UI's optional IdP redirect is `VITE_SIGN_IN_URL`, supplied at frontend build time; absent it, unauthenticated users see a manual sign-in instruction rather than a redirect to an organization-specific URL. If an edge needs a cookie/login flow, provide that URL and configure the corresponding logout endpoint. MCP also accepts app-issued `phk_` tokens; token ownership always takes precedence over forwarded headers. The device approval endpoints refuse app tokens, requiring a browser identity.

## Prerequisites

PostgreSQL and an external authenticated reverse proxy for any non-local use. No proprietary SDK is bundled. If you distribute a container with psycopg's binary wheel, follow its LGPL-3.0 obligations described in [NOTICE](NOTICE).

## License

Original code is licensed under [Apache-2.0](LICENSE). See [NOTICE](NOTICE) for third-party dependency obligations.

## Contributing

See [CONTRIBUTING](https://github.com/exowatt-oss/.github/blob/main/CONTRIBUTING.md). Contributions require DCO sign-off (`git commit -s`); no CLA is required.

## Security

Please report vulnerabilities privately using [GitHub private vulnerability reporting](https://github.com/exowatt-oss/phi-hub/security/advisories/new), or contact software@exowatt.com. See the [security policy](https://github.com/exowatt-oss/.github/blob/main/SECURITY.md); do not open a public issue.
