# CDM Ops v1 (patched)

A self-hosted replacement for the Battlemage Google Apps Script collection tool.

This is a patched version of the original CDM Ops v1 delivery. See
`CHANGELOG.md` for exactly what changed and why.

## Stack
- PostgreSQL 16
- FastAPI + SQLAlchemy
- Jinja2 responsive web UI
- Separate Stern Playwright scraper
- Local raw JSON snapshots
- Daily PostgreSQL backups
- Password-gated login (session cookie) in addition to perimeter auth

## Deploy on Unraid
1. Copy this folder to a share, e.g. `/mnt/user/appdata/cdm-ops`.
2. `cp .env.example .env`
3. Edit `.env` and set the three long random secrets (`POSTGRES_PASSWORD`, `APP_SECRET`, `SCRAPER_API_KEY`).
4. Put the legacy `.xlsx` files in `imports/`.
5. Start: `docker compose up -d --build`
6. Set the login password: `docker exec -it cdm-ops python scripts/hash_password.py`, paste the printed hash into `.env` as `APP_PASSWORD_HASH`, then `docker compose up -d` again to pick it up.
7. Seed: `docker exec cdm-ops python scripts/seed.py`
8. Import legacy data: `docker exec cdm-ops python scripts/import_legacy.py`
9. Open `http://YOUR_UNRAID_IP:8080` and log in with the password you set in step 6.

Do not expose PostgreSQL directly. If you want remote access, expose only the web app through your existing Cloudflare Tunnel — Cloudflare Access should still be your primary gate; the app-level login here is a second layer in case that tunnel config ever gets loosened or skipped, not a substitute for it.

## Stern scraper
The scraper deliberately has no credentials in source code. Set:
`STERN_EMAIL`, `STERN_PASSWORD`, `STERN_LOCATION_URL`, `CDM_API_URL`, `SCRAPER_API_KEY`.

Build a separate scraper container or run the script from the existing NAS scheduler. It writes a raw JSON snapshot before posting normalized data to CDM Ops.

The scraper identifies which machine a Stern row belongs to by matching the row's title text. This is inherently fragile if Stern changes their page. Set `STERN_TITLE_ID_MAP` in `.env` (a JSON object mapping title text to Stern machine ID) for every machine you can — it skips the guesswork entirely for anything listed there. Unmapped titles fall back to a heuristic and log a warning; check the scraper's logs occasionally for those warnings.

## Adding Pokémon
Use Machines -> Add machine. Give it its Stern machine ID and Battlemage location. No code change is required. Star Wars remains historical data; it does not need to be renamed.

## Moving a machine
Use Machines -> (machine) -> Move machine, with the new location and an effective date. This closes out the machine's current `machine_location_history` row and opens a new one — past plays, collections, and service records keep pointing at the location they actually happened at, even after the move.

## Location-facing collection portal
Each location has a permanent, unguessable link (shown on the Locations admin page and on the Collection entry page) that shows a running statement of that location's own collections — no login needed, and nothing about other locations, purchase costs, or partner data. Send it once; new collections just show up. If a link ever leaks, use "Regenerate portal link" on the Locations page to kill it.

## Security
The old uploaded project contained a plaintext Stern account email/password. Rotate that password before using the new scraper and never commit credentials to Git.

`APP_PASSWORD_HASH` is a bcrypt hash, not the plaintext password — generate it with `scripts/hash_password.py`, never type a plaintext password directly into `.env`.
