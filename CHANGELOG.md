# Patch notes — CDM Ops v1

What changed from the delivered zip, and why. Nothing here touches the overall
approach (FastAPI + Postgres + Jinja2 + Docker) — that stack is sound. These
are fixes and gaps closed against the original brief.

## 1. Machine location history (the big one)

**Problem:** `Machine.location_id` was a single overwritable column. Moving a
machine to a new venue would have silently lost the ability to attribute past
plays/collections to the venue they actually happened at — the exact thing
§5 and §19 of the brief call out by name.

**Fix:**
- Added `machine_location_history` (machine, location, start_date, end_date).
- `Machine.location_id` is now explicitly documented as a *cache* of "current
  location," written only by `open_machine_location()` — never set directly.
- Added `location_id_at(machine_id, date)` to resolve historical location for
  reporting.
- Added `PlaySnapshot.location_id`, stamped at ingest time via
  `location_id_at()`, so "plays at Battlemage in June" stays answerable after
  a machine moves.
- Added `POST /machines/{id}/move` + a "Move machine" form on the Machines
  page, and a "Location history" expander showing the trail.
- `seed.py` and `import_legacy.py` updated to open the initial history row
  instead of writing `location_id` directly.

## 2. Static files weren't served

`base.html` links `/static/style.css`, but `main.py` never mounted the
static directory — the app would have loaded with no CSS. Added
`app.mount("/static", StaticFiles(directory="app/static"), name="static")`.

## 3. No login was actually wired up

`bcrypt`/`passlib` were dependencies and `APP_SECRET` was a required env var,
but nothing used either — there was no login. Added a session-cookie login
(`SessionMiddleware` + a password gate checked with `passlib.hash.bcrypt`) in
front of every page except `/login`, `/api/health`, and the two
API-key-protected endpoints. Cloudflare Access in front of the tunnel should
still be the primary control per the README — this is a second layer so the
app isn't wide open if that ever gets misconfigured.

Set it up with `scripts/hash_password.py` (new file) — generates a bcrypt
hash to paste into `.env` as `APP_PASSWORD_HASH`. The plaintext password is
never written to disk.

## 4. Scraper machine-ID matching was a guess with no way to notice when it's wrong

The scraper matched Stern rows to machines by extracting trailing digits from
the row's title text — reasonable as a fallback, risky as the only method,
and it fails silently.

- Added `STERN_TITLE_ID_MAP`, an env var (JSON) mapping known title text to
  Stern machine IDs. Set for D&D/Star Wars/Pokémon and it skips the guesswork
  entirely.
- Unmapped titles still fall back to the digit heuristic, but now print a
  warning to stderr so it shows up in scraper logs instead of failing
  quietly.
- `/api/plays/ingest` now reports any `stern_machine_id` it didn't recognize
  in its response and server logs, instead of silently dropping that row.

## 5. Same-day double-ingest guard

If the scraper ever runs twice in one day, Stern's "today" figure would have
been added to the cumulative odometer twice. `/api/plays/ingest` now skips a
row if a snapshot already exists for that machine on that UTC date.

## 6. Location-facing collection portal

The original delivery had no equivalent of the old "link to a spreadsheet"
receipt for venues. Added a read-only portal, scoped per-location rather
than per-collection (see the chat thread for why): a long random token lives
on `locations.access_token`, and `GET /portal/{token}` shows a running
statement of that location's collections — machine, plays since their last
collection, revenue, gross/venue-share/CDM-share, who collected — nothing
about other locations, purchase costs, or partner splits. New collections
just appear; nothing needs to be regenerated or re-sent per visit.

- `Location.access_token`, auto-generated on creation; a startup backfill
  handles any existing rows (see the `ALTER TABLE` note in `main.py` if
  you're deploying this on top of a database that predates this column).
- `POST /locations/{id}/regenerate-portal-link` to kill a leaked link.
- The Locations admin page shows each location's portal link + a regenerate
  button; the Collection entry page shows the currently-selected location's
  portal link too, for quick copy-paste after logging a visit.
- Portal responses set `Cache-Control: no-store` and `X-Robots-Tag: noindex`
  since this is a permanent link, not a one-time share.
- An unrecognized token 404s generically — it doesn't distinguish
  "malformed" from "not found," so it can't be used to probe for valid
  tokens.


- `Partner`, `LedgerEntry`, `Purchase.paid_by`/`trued_up` are scaffolded but
  have no routes/UI yet — that's fine, matches the brief's own Phase 3
  sequencing. Worth flagging so nobody assumes true-ups are already working.
- Postgres, Docker Compose layout, backup cron, and the scraper's overall
  Playwright approach are unchanged — no issues found there.
- Untested end-to-end (no network access in this environment to install
  dependencies and run it). Syntax-checked (`py_compile`) and reviewed
  line-by-line, but run it in a scratch environment before pointing it at
  real collections.

## Before you deploy

- The partner ownership percentages in `seed.py` (Felix 42.5%, Matt 42.5%,
  Sean 15%) and the Stern machine IDs (397061, 279284) came from the prior
  ChatGPT conversation, not from anything in the files I was given — worth a
  quick confirm that those are right before they go live.
