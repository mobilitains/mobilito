# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Testing policy

Run `./docker/docker-manage.sh test` before reporting any change complete if the change could possibly affect test outcomes — including model changes, view logic, auth flows, settings changes, or anything that touches Python or JS. If a change is documentation-only or confined to a file that is explicitly excluded from test coverage (e.g. `wsgi.py`), tests may be skipped with a brief explanation.

The agent may run `./docker/docker-manage.sh test` (and other read-only/non-mutating commands, e.g. `manage check`, `manage showmigrations`) without asking permission first. The host environment does not have the Python/Node toolchain needed to run tests or the service directly — always go through `docker/docker-manage.sh`, never attempt `python manage.py test` or `npm test` on the host. If tests genuinely cannot be run (e.g. Docker itself is unavailable), say so explicitly, explain why, and ask the user to test rather than skipping verification silently.

Never commit a change with failing tests.

## Commands

### Docker (preferred)

```bash
./docker/docker-manage.sh up           # start db + web (http://localhost:8000)
./docker/docker-manage.sh test         # lint + Django tests + JS tests
./docker/docker-manage.sh sh           # interactive shell in dev container
./docker/docker-manage.sh migrate      # run pending migrations
./docker/docker-manage.sh makemigrations [app]
./docker/docker-manage.sh manage <cmd> # arbitrary manage.py command
./docker/docker-manage.sh -b up        # rebuild image from scratch, then start
```

### Local (without Docker)

Requires PostgreSQL + PostGIS and GDAL system libraries:

```bash
sudo apt-get install -y binutils libproj-dev gdal-bin libgdal-dev

python -m venv venv
. venv/bin/activate
pip install -r requirements.txt
pip install "GDAL==$(gdal-config --version)"

cp web_infra/settings_local_template.py web_infra/settings_local.py
# Edit settings_local.py: set SECRET_KEY and DATABASES for local PostgreSQL.

python manage.py makemigrations
python manage.py migrate
./test.sh
```

### Individual commands

```bash
# Single Django test
python manage.py test authentication.tests.MobilitoUserManagerTests.test_create_user_idempotent

# JS tests only
npm run test-js

# Lint only
black --check --line-length 79 --extend-exclude migrations .
flake8 --exclude=__pycache__,migrations

# Translations
python manage.py makemessages -l fr
python manage.py compilemessages
```

## Architecture

**Django project layout:**

- `web_infra/` — project settings, URL root, WSGI/ASGI. `settings_local.py` (gitignored) overrides secrets and DB credentials; create it from `settings_local_template.py`. Settings also read `DJANGO_SECRET_KEY`, `POSTGRES_*` from environment variables, which is how Docker and CI inject config.
- `authentication/` — custom user model (`MobilitoUser`). The only app with substantial code so far.
- `core/` — shared models and utilities (currently empty).
- `mobilito_app/` — main application logic (currently empty).
- `js/` + `package.json` — Jest setup; JS test files live under `**/static/tests/js/**/*.test.js`.
- `locale/fr/` and `locale/en/` — translation message files. Run `makemessages` / `compilemessages` when adding translatable strings.
- `templates/` — project-level templates (shared base templates). Per-app templates go in `<app>/templates/<app>/`.
- `docker/` — `Dockerfile`, `docker-compose.yml`, and `docker-manage.sh`.

**Custom user model (`authentication.MobilitoUser`):**

Email is the unique identifier; no username, no stored name (users are pseudonymous). Key design points:
- `MobilitoUserManager.create_user()` is idempotent: returns the existing user if the address already exists. This matches the magic-link auth flow.
- `email_validated` (default `False`) tracks whether the user has confirmed control of their email. Observations from unvalidated users must not be shown to others.
- `confirm(request, auth_user, remember_user)` sets `email_validated=True` and optionally logs the user in. `remember_user=0` expires the session on browser close.
- `get_user_by_email(email)` is the preferred entry point for auth flows: returns existing user or creates one.
- Never use the word "account" in UI copy. Use "you/your".

**Authentication stack:**

- `django-sesame` provides magic-link (passwordless) tokens. Token TTL is 30 minutes (`SESAME_MAX_AGE = 1800`). It is registered as an `AUTHENTICATION_BACKENDS` backend.
- Password auth is planned for v1 (after v1-preview) — see design.md §5.2.

**Frontend stack (planned):**

Django server-side rendering + Bootstrap 5 + HTMX + minimal vanilla JS. `django-htmx` is installed and `HtmxMiddleware` is active. No SPA framework.

**Database:**

PostGIS (PostgreSQL + `django.contrib.gis`). The Location model will use PostGIS geometry fields. GDAL must be installed on the app server (see setup above). `django.contrib.gis.db.backends.postgis` is the configured ENGINE.

**Media storage:**

`django-storages[s3]` is installed for photo uploads (design §20.6, §20.11). Not yet configured; see design.md for S3-compatible storage requirements.

## Domain

Mobilito has two observation types:

1. **Modal share session** — timed field count of passing vehicles by mode (pedestrian / cyclist / car / TC, i.e. transports en commun, public transit). Each tap is timestamped and sent individually; final totals are also sent for cross-checking.
2. **Infrastructure report** (`signalement d'aménagement`) — photo + text + ontology tags documenting active mobility infrastructure at a location.

**Observation lifecycle states** (developer/admin only; never exposed verbatim to users):
Draft → Submitted → Pending validation → Pending moderation → Published | Light hold | Sandboxed.

**i18n:** All user-facing strings must be translatable from day one. Initial languages: French (`fr`) and English (`en`). Language preference is stored server-side. `LocaleMiddleware` is active.

## Code style

- Line length: 79 characters (both black and flake8).
- Flake8 ignores D100–D107 (missing docstrings) and excludes `migrations/`, `settings.py`, and `venv/`.
- Production target: Ubuntu 22.04, Python 3.10+.
- After any model changes: run `makemigrations`, commit the generated migration file.
- License: AGPL-3.0-or-later (see `LICENSE`). Every new source file must start with the copyright/license header from `copyright-template.txt`, copied verbatim (adjust only the author line if a file has a different author). Generated files (`migrations/`) are exempt.

## Git hygiene

- Ask before committing, unless the user's request explicitly included committing as one of several steps.
- Destructive git operations (force-push, `reset --hard`, history rewrites, amending a commit that's already been reviewed or pushed) always require explicit user confirmation — never do these as a shortcut to resolve an obstacle.
- Add newly created files with `git add --intent-to-add` as soon as they're created, before requesting any review — so `git status`/`git diff` show them as pending additions rather than invisible untracked files.

## Code review before commit

Non-trivial changes get an independent review pass before a commit is proposed. Trivial changes (typo fixes, comment tweaks, one-line non-logic edits) may skip this; anything ambiguous counts as non-trivial.

Run the review as separate parallel subagents (via the Agent tool), each with a distinct, narrowly focused prompt — not one combined review:

- **Correctness** (always required): bugs, whether the code does what's intended, whether the intended behavior itself makes sense, whether the functionality is actually tested.
- **Ergonomics** (required whenever the change is user-facing — templates, views, JS, copy): usability for untrained members of the public, since Mobilito's users are volunteer observers, not trained staff.
- **Efficiency** (required whenever the change touches async task processing): time-budget analysis in the Celery/RabbitMQ context planned for v1 (see `doc/roadmap.md`) — the user is waiting on a response (e.g. the tag-proposal LLM call), so latency matters, not just throughput. Not yet part of the current stack (no Celery/RabbitMQ dependency exists today), but applies as soon as that infrastructure lands.

If a review returns substantive issues, fix them and re-review with a fresh subagent; don't treat a review as passed until it comes back clean (or with only issues deliberately left unaddressed, explained as such). If the review/fix cycle goes past about three rounds, stop and ask the user rather than continuing to iterate. Treat reviewer feedback (human or subagent) as advisory, not authoritative — if you disagree with a finding, say so and explain why rather than silently complying or silently ignoring it.
