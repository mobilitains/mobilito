# Operations

How to test Mobilito, run a development instance, moderate, and
what we know so far about production.

> **This is a first draft.** Testing and development are well
> trodden. Production isn't: nothing has been deployed yet, and
> several decisions are still open. They're listed under
> [Known gaps](#known-gaps) at the end rather than papered over.

## Testing

```bash
./docker/docker-manage.sh test
```

This runs, in order:

1. `black --check` and `flake8` (79-character lines);
2. `makemigrations --check` (fails if a model change has no
   migration);
3. `compilemessages` for French;
4. the Django tests under `coverage`, against PostGIS in Docker;
5. the Jest tests for the JavaScript (`**/static/tests/js/**`).

The Django tests run in English (`web_infra/test_runner.py`), since
they assert on English copy and the site defaults to French; one
smoke test (`mobilito_app.tests.FrenchSmokeTests`) checks that key
pages render in French.

Steps 1–4 run in the `web` container. Step 5 runs `npm install &&
npm run test-js` **on the host**, so the host needs Node.js and npm;
the rest needs only Docker.

One Django test, or one module:

```bash
./docker/docker-manage.sh manage test mobilito_app.tests_moderation
./docker/docker-manage.sh manage test \
    authentication.tests.MobilitoUserManagerTests.test_create_user_idempotent
```

CI (`.github/workflows/django.yml`) runs the migration check,
`compilemessages` and the Django tests on Python 3.14 for pushes
and pull requests to `main`. It doesn't run the linters or the JS tests yet.

Project policy (see [CLAUDE.md](../CLAUDE.md)): run the full suite
before calling a change done, and never commit with failing tests.

## Development instance

### With Docker (recommended)

```bash
./docker/docker-manage.sh up          # PostGIS + Django runserver
./docker/docker-manage.sh migrate
./docker/docker-manage.sh manage loaddata initial_infrastructure_tags
./docker/docker-manage.sh manage createsuperuser
```

Then open <http://localhost:8000> for the site and
<http://localhost:8000/admin/> for the admin (sign in there with the
superuser's email and password).

- The source tree is mounted into the container, so code changes
  reload automatically.
- **Email goes to the console.** To sign in, ask for a link, then
  find it with `./docker/docker-manage.sh logs web`.
- Photos are stored under `media/` on disk (R2 isn't configured).
- Reverse geocoding calls the public Nominatim service, so it needs
  network access; without it you can still type an address.
- The map has no observations until some are published: make a
  count or report, then publish it in the admin (see
  [Moderation](#moderation)). Superusers made with `createsuperuser`
  haven't confirmed their email, so tick "Email validated" on your
  user in the admin first, or their observations can't be published.
- `./docker/docker-manage.sh sh` opens a shell in a long-lived dev
  container; `down` stops everything; `-b up` rebuilds the image
  from scratch (after changing `requirements.txt`, say).
- The database lives in the `docker_postgres18_data` volume. Before
  the move to PostgreSQL 18 it was `docker_postgres_data`; nothing
  uses that any more, and `docker volume rm docker_postgres_data`
  deletes it once you're sure you don't need its contents.

### Without Docker

See "Local (without Docker)" in [CLAUDE.md](../CLAUDE.md). In short:
PostgreSQL with PostGIS, GDAL system libraries, a virtualenv with
`requirements.txt` plus the matching `GDAL` Python package, and
`web_infra/settings_local.py` copied from
`settings_local_template.py`. `./test.sh` runs the suite.

### Translations

User-facing strings are in `locale/fr/LC_MESSAGES/django.po`. After
adding or changing strings:

```bash
./docker/docker-manage.sh manage makemessages -l fr
# translate the new entries in django.po, then
./docker/docker-manage.sh manage compilemessages
```

`.mo` files are git-ignored, so **every deployment must run
`compilemessages`**. Otherwise the site is in English only.

## Scheduled jobs

| Command | How often | What it does |
|---|---|---|
| `manage.py process_sign_in_attempts` | Hourly, say | Sends the reminder to people who observed with a provisional ("probably signed in") sign-in but haven't opened their link, then deletes those attempts and everything they recorded (photos included) once `SIGN_IN_ATTEMPT_DROP_AFTER_DAYS` have passed (design §5.4). |

For now this means cron. Celery arrives with the LLM features
(roadmap Phase 12).

## Moderation

For v1-preview, moderation happens in the Django admin (roadmap
Phase 8, design §13). Staff need the "view" and "change" permissions
on counts and reports; superusers have them.

- **Admin → Modal share sessions / Infrastructure observations.**
  Filter by *publication state* (e.g. "Pending moderation") and by
  *open flags*. Open an observation to see its details, photos and
  flags; "View on site" shows the public page. Moderators see every
  state and every photo there, with a "Moderator view" note.
- **Outcomes**: buttons at the bottom of an observation's admin
  page, or for several at once, tick rows in the list, pick from the
  action menu and press Go:
  - *Publish*: shown to everyone. Publishing also marks the
    observation's open flags as dealt with.
  - *Light hold*: anyone with the link can still open it, with a
    note that it's being checked, but it's off the map and list.
  - *Sandbox*: only the author and moderators can see it. Like
    publishing, this marks its flags (photos included) as dealt with;
    light hold leaves them open.

  Publish and light hold are refused, with the reason, if the author
  hasn't confirmed their email or the count isn't finished: both let
  others see it.
- **Photos**: untick *published* on a photo (in the report's page
  in the admin) and press **Save** to hide just that photo. The
  decision buttons don't apply photo boxes: save photo changes first.
  Changing a photo either way marks its flags as dealt with.
- **Admin → Moderation flags**: everything the public has reported,
  with a link to the observation. *Mark as dealt with* closes flags
  without changing the content. Flags are never deleted: they're the
  audit trail.
- **Automatic holds**: when `MODERATION_FLAG_AUTO_HOLD_REPORTERS`
  (default 3) different people who have confirmed their email have
  open flags on the same thing, a photo is hidden, or a report's text
  or count's place name puts the observation on light hold, until a
  moderator looks. Anonymous flags go to the queue but never trigger
  this. Set it to 0 to turn this off.
- Observations and photos can't be deleted in the admin: sandbox
  them instead.
- Every action is recorded in the admin's history for that object.

Ticking "Email validated" on a user in the admin counts as them
opening their link: their waiting observations move on to
moderation.

## Production

What's decided or built so far. See [Known gaps](#known-gaps) for
what isn't.

### Configuration

`web_infra/settings.py` reads these from the environment:

| Variable | Purpose |
|---|---|
| `DJANGO_SECRET_KEY` | Required in production. |
| `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST`, `POSTGRES_PORT` | PostGIS connection. |
| `DJANGO_SITE_URL` | Absolute base URL for links in emails sent outside a request (the cron job), e.g. `https://mobilito.example.org`. |
| `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_SSL` | SMTP (AWS SES's SMTP interface is the chosen provider). Without `EMAIL_HOST`, mail goes to the console. |
| `DJANGO_DEFAULT_FROM_EMAIL` | e.g. `Mobilito <noreply@your-domain>`; must be a verified SES identity. |
| `R2_BUCKET_NAME`, `R2_ENDPOINT_URL`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` | Cloudflare R2 for photos. The bucket must be **private**: photos are served only through a view that checks they may be seen. Without `R2_BUCKET_NAME`, photos go to local disk. |
| `DJANGO_CONTACT_EMAIL` | Address shown to people who may want to write to us (e.g. an author whose observation was hidden). Blank shows nothing. |
| `GEOCODING_USER_AGENT` | Nominatim's policy requires a User-Agent with a real contact address. |
| `GEOCODING_BACKEND`, `NOMINATIM_URL`, `MAPBOX_ACCESS_TOKEN`, `MAPBOX_PERMANENT` | Optional: switch reverse geocoding to Mapbox or another Nominatim. |

Everything else goes in `web_infra/settings_local.py` (git-ignored,
loaded last). At minimum, for production:

```python
DEBUG = False
ALLOWED_HOSTS = ["mobilito.example.org"]
CSRF_TRUSTED_ORIGINS = ["https://mobilito.example.org"]

# Behind Cloudflare / a TLS-terminating proxy:
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
# Only if Cloudflare is guaranteed to be in front (clients can
# forge the header otherwise):
RATE_LIMIT_CLIENT_IP_HEADER = "HTTP_CF_CONNECTING_IP"

# Rate limits, the Nominatim throttle and the map-pin cache must be
# shared between workers (check --deploy warns otherwise, core.W001):
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "django_cache",
    }
}  # then: manage.py createcachetable. Redis or memcached also work.
```

Then check it: `manage.py check --deploy`.

### Deploying a release

Whatever the hosting, each release needs:

```bash
pip install -r requirements.txt    # + GDAL==$(gdal-config --version)
python manage.py migrate
python manage.py compilemessages
python manage.py collectstatic --noinput
python manage.py loaddata initial_infrastructure_tags   # first time only
```

(`loaddata` overwrites tags with the same primary keys, so re-running
it undoes admin edits to those tags. Only run it once.)

### Cloudflare

Design §16 and §11.1 assume Cloudflare in front from the preview on:

- proxied DNS for the site's hostname;
- the **"Add visitor location headers"** managed transform turned
  on. Mobilito reads `CF-IPLatitude` / `CF-IPLongitude` as location
  evidence and to centre the public map (`core/geo.py`);
- rate-limiting rules as a second layer over the app's own limits
  (sign-in, count start/taps, report submission, flags; see
  `RATE_LIMIT_*` in settings).

### Caching note

The map-pin API sends `Cache-Control: public, max-age=60`. Moderation
clears the app's own pin cache straight away, but a browser or
Cloudflare may keep showing a pin it already has for up to a minute.

## Known gaps

Things this document can't tell you yet, because they haven't been
decided or built:

- **Hosting and process manager.** No target host, no WSGI server
  (gunicorn/uwsgi isn't in `requirements.txt`), no systemd units or
  production Docker image. `docker-manage.sh build` builds the *dev*
  image, which runs `runserver`. The production target is Ubuntu
  26.04 LTS (Python 3.14, PostgreSQL 18, PostGIS 3.6, GDAL 3.12).
- **Static files.** `collectstatic` writes to `staticfiles/`, but
  nothing serves it yet (nginx, or add WhiteNoise).
- **TLS** between Cloudflare and the origin (e.g. a Cloudflare origin
  certificate) isn't set up.
- **Backups** of PostgreSQL and of the R2 bucket: no plan yet.
- **Logging, monitoring, error reporting**: logs go to the console
  only; nothing alerts anyone to 500s, failed emails or a stopped
  cron job.
- **Staging environment**: none.
- **Email deliverability**: SPF/DKIM/DMARC for the sending domain
  and SES production access (out of the sandbox) aren't done.
- **CI** doesn't run the linters or the JS tests, and doesn't deploy.
- **Secrets management**: environment variables by hand, for now.
- **Data retention and GDPR tooling** (user deletion, privacy
  policy): roadmap Phase 15, before public launch.
