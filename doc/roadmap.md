# Mobilito — Implementation Roadmap

Path from the current skeleton to v1-preview (internal testing) and v1 (public launch).
References are to `design.md` sections.

---

## Current state

The skeleton provides:
- Custom user model with `email_validated` flag, magic-link auth (django-sesame), PostGIS backend, Bootstrap 5 + HTMX + django-htmx wired in, django-storages[s3] (configured for Cloudflare R2) and Pillow installed, i18n scaffold, Docker dev environment, CI.

Nothing is user-visible yet.

---

## Decisions required before coding begins

These unblock multiple phases and should be settled first.

| Decision | Options | Notes |
|---|---|---|
| **Email provider** | Postmark, Mailgun, AWS SES | Magic links don't work without one. Configure `EMAIL_BACKEND` in settings. **Decision: Assume AWS SES.** |
| **Map tiles** | OpenStreetMap + Leaflet.js | No API key; free at any scale; strong mobile support. Suitable for v1. **Decision: yes.** |
| **Reverse geocoding** | Nominatim (OSM) | Free but rate-limited. Wrap in a thin service layer so the provider can be swapped without touching views. **Decision: Yes.  Assume we may also use mapbox for reverse geocoding, with an attempt to stay within their free tier.** |
| **Object storage** | Cloudflare R2 | django-storages[s3] installed; R2 is S3-compatible and has no egress fees. Configure credentials before Phase 6. **Decision: Yes.** |
| **Async task queue** | Celery + RabbitMQ | Needed for LLM calls (v1). RabbitMQ's push-based delivery keeps task pickup latency in milliseconds — important because the tag-proposal LLM call is a user-waiting interaction (page polls for result). Redis-as-broker has weaker acknowledgment semantics and would add unreliability overhead. Add to docker-compose when Phase 12 starts; not needed for v1-preview. **Decision: Yes.** |
| **Javascript** | HTMX | Do we have opinions on javascript frameworks? **Decision: Use htmx to the extent possible.  Avoid to the extent possible anything that requires a large auxiliary library load.** |

---

## Phase 1 — Core data models

Everything else is built on top of these. Define all models, write migrations, seed initial data.

**User model additions** (extend `authentication.MobilitoUser`):
- `preferred_language` (choices: fr / en; default: null = use Accept-Language)
- `use_device_location` (bool; default True) — server-persisted geolocation preference (§11.2)

**`core` app models:**

- **`Location`** — PostGIS `PointField`, user-entered address, reverse-geocoded address, area metadata (country, région, département, commune). Location equivalence radii in Django settings (not hardcoded) (§11.4, §20.2).
- **`LocationEvidence`** — device GPS, user-adjusted coords, Cloudflare geo, EXIF geo, accuracy metadata, timestamp; FK to observation (§20.10).

**`mobilito_app` app models:**

- **`ModalShareSession`** — user, location, started/finished at, publication state, per-mode totals, integrity hash (§20.3).
- **`ModalShareCountEvent`** — session, timestamp, mode (ped/bike/car/tc), optional lat/lon at tap time (§20.4).
- **`InfrastructureObservation`** — user, location, observer perspective, description, publication state, moderation state (§20.5).
- **`InfrastructureMedia`** — observation, storage reference, EXIF geo, moderation state, published flag (§20.6). Use django-storages `ImageField`.
- **`InfrastructureTag`** — label (per locale), description (per locale), geography scope, ontology family, status. Use `django-modeltranslation` for translated fields so admins can manage copy without code changes (§10, §20.7).
- **`ObservationAction`** — parent observation, action type (me-too/progress/degradation/resolved/additional-issue), text, created by, cancelled at (§20.8).
- **`ContactMethod`** — geography scope, contact type, contact value, do-not-contact flag, optional `user_id` link (§20.9).
- **`ModerationFlag`** — target object (generic FK to observation or media), reporter, reason, created at (§13.4). Pulled forward from Phase 8 so all models are defined in one place per this phase's stated goal.

**Publication state** is a string choices field shared by both observation types (Draft / Submitted / PendingValidation / PendingModeration / Published / LightHold / Sandboxed) (§14).

**Initial data fixture**: seed a core set of `InfrastructureTag` records in both FR and EN before v1-preview.

**Deferred user fields**: §20.1 also lists bot-risk/trust signals and moderation history on `MobilitoUser`. No concrete schema for these yet — add them incrementally as the features that need them land (flagging in Phase 8, moderator role in Phase 13) rather than guessing the shape now.

---

## Phase 2 — Base UI shell

Before building any feature page, establish the shared shell that all pages inherit from.

- `base.html`: Bootstrap 5 (CDN for now; bundle later), HTMX, mobile viewport meta, Bootstrap Icons.
- Navigation: logo, language switcher (HTMX form post → server stores preference → redirect), auth state (sign-in link or "signed in" indicator).
- Language switching: store `preferred_language` on the user if authenticated, otherwise in the session. `LocaleMiddleware` picks it up.
- Landing page (`/`): brief pitch, two CTAs — *Browse* and *Make an observation* — per §21.1.
- Authenticated home screen (§21.2): once signed in, replace the landing-page CTAs with two large side-by-side buttons — *Count modal share* and *Report an aménagement* — plus links to the user's own past observations and the public map/browse view.
- Error pages: 404, 500.
- All strings wrapped in `_()` / `{% trans %}` from the first template.

**i18n workflow**: run `makemessages -l fr` after each phase; keep `.po` files committed and up to date.

---

## Phase 3 — Authentication flows

`django-sesame` is already installed. This phase wires it to views and email.

- **Magic-link request** (`/auth/start/`): email input form, honeypot field, rate limit (§16). On submit: get-or-create user, send magic-link email.
- **Magic-link validate** (`/auth/verify/<token>/`): sesame validates token, calls `user.confirm(request, auth_user=True)`, redirects. Sets `email_validated = True`.
- **Sign out** (`/auth/logout/`): standard Django logout.
- **Email template**: plain-text + HTML, localised, includes link to observation if one was in progress.
- **Session handling**: session expires on browser close unless user checks "remember me" (maps to `confirm(remember_user=1)`).
- **Unvalidated user banner**: template tag that renders a gentle prompt if `request.user.is_authenticated and not request.user.email_validated`. Never uses the word "account".
- Configure `EMAIL_BACKEND` and `DEFAULT_FROM_EMAIL` in settings.

---

## Phase 4 — Map component

A reusable component used by observation submission (both types), browsing, and observation detail pages. Build it once cleanly.

- Leaflet.js loaded from CDN (or bundled with `django-compressor` later).
- `map_widget.html` include: takes a Django template context with initial centre, zoom, optional observation pins GeoJSON, and configuration flags (crosshair mode on/off, GPS button on/off).
- **Crosshair mode** (§9.2): fixed SVG crosshair overlay centred in the viewport. Map centre = reported location. "Confirm location" button posts current map centre to the server.
- **GPS button**: calls `navigator.geolocation.getCurrentPosition()` only if `use_device_location` is True; pans map to result. Shows the "location-unverified" notice if geolocation is off or denied (§11.2).
- **Observation pins**: coloured Leaflet markers rendered from a GeoJSON endpoint. Tapping a pin triggers a bottom-sheet partial loaded via HTMX.
- **Bottom sheet**: Bootstrap offcanvas component anchored to the bottom; loaded by HTMX from `/observations/<id>/summary/`.
- Reverse geocoding: on "Confirm location", fire an async request to a wrapper view that calls Nominatim; return human-readable address for user to review/edit.

---

## Phase 5 — Modal share counting

Full counting workflow (§9.1, §21.3).

**Server side:**
- `POST /counts/start/` — create `ModalShareSession` (state: Draft), return session id.
- `POST /counts/<id>/event/` — append `ModalShareCountEvent`; accept JSON `{mode, client_timestamp, lat, lon}`. Rate-limited.
- `POST /counts/<id>/finish/` — receive final totals, compute integrity hash, transition state to Submitted → PendingValidation (if unvalidated) or PendingModeration (if validated).
- `GET /counts/<id>/` — observation detail/results page.

**Client side (vanilla JS, ~200 LOC):**
- Four full-width square tap buttons (ped / bike / car / tc) with mode pictograms and running counts.
- Each tap: increment local counter, record `{mode, timestamp: Date.now()}`, attempt `POST /counts/<id>/event/`. On network failure: queue in `localStorage`, retry on reconnect.
- Sync-on-reconnect: `navigator.onLine` listener flushes the queue. Show "syncing…" banner while pending.
- Minimum session duration check on Finish (named constant `MIN_SESSION_SECONDS = 120`, configurable in settings) — if short, show "Keep it or discard?" prompt (§9.1).
- GPS flagging: if device location differs from stated location by more than a configurable threshold, flag session `location_mismatch = True` in the DB (visible in admin; noted publicly on the observation).

**Results page:**
- Bar chart of mode totals (HTML/CSS, no JS library needed at this scale).
- "Do another count here" button (pre-fills location from current session).
- Unvalidated user: gentle prompt to check email.
- Share link (direct URL to this observation).

Note that a POC of this was built at ~/src/jma/transport-nantes/tn_web/transport_nantes/mobilito/ .
Use that code to inspire you here.  The only part of that UX that is particularly confirmed by the POC is the large four-button grid for counting.

---

## Phase 6 — Infrastructure observations

More complex than modal share due to photo handling and the ontology (§9.2, §21.4).

**Server side:**
- `GET /reports/new/` — map screen with crosshair (Phase 4 component, crosshair mode on).
- `POST /reports/new/location/` — HTMX: receive confirmed lat/lon, render next step (role selection) in page without full reload.
- Role selection: pedestrian / cyclist / both (same large-button UI as modal share, but selects a value rather than incrementing).
- Photo upload: multi-file `<input type="file" accept="image/*">`. On submit, `Pillow` validates images; `django-storages` writes to S3. Extract EXIF geo with Pillow; store in `InfrastructureMedia.exif_geo`. Reject files that aren't images.
- `POST /reports/new/submit/` — create `InfrastructureObservation` + media + `LocationEvidence`. Transition to Submitted → PendingValidation or PendingModeration.
- Tag selection: render active `InfrastructureTag` records filtered by geography scope matching the observation's country. Checkbox list for v1-preview; HTMX autocomplete in v1.
- Honeypot field on submission form (§16).

**Client side:**
- Form state persistence in `sessionStorage` so partial drafts survive accidental navigation (§18).
- Photo preview before upload.

---

## Phase 7 — Observation browsing

Public, requires no authentication (§9.3, §21.1).

- `GET /map/` — full-page Leaflet map with published observation pins (GeoJSON endpoint filtered to `state=Published`). Clusters at low zoom. No geolocation required.
- `GET /observations/` — list view of recent observations near a given point (or all, paginated). Works without JS.
- `GET /observations/<id>/` — full detail: photos, text, tags, history, me-too count, share button.
- GeoJSON API endpoint (`/api/observations.geojson`) used by the map: returns published observations within a bounding box. Cached (e.g. 60 s) to handle multiple simultaneous viewers.
- Own observations list for authenticated users: `/observations/mine/` — shows all their observations including those not yet published, with their current state in plain language.
- **Location time-series view** (§9.1): for a given `Location`, aggregate and chart modal share counts across all sessions linked to it over time. This is listed as a required "must support" capability in Core Features, not a nice-to-have — give it its own view (e.g. on the location/observation detail page) rather than letting it fall out of scope.

---

## Phase 8 — Moderation and publication lifecycle

Minimal for v1-preview (admin-accessible only); full dashboard in v1 (§13).

**Publication state machine** — transitions:
- Draft → Submitted (on form submit)
- Submitted → PendingValidation (if unvalidated) or PendingModeration (if validated)
- PendingValidation → PendingModeration (when `email_validated` becomes True)
- PendingModeration → Published (admin or moderator action)
- Any state → LightHold / Sandboxed (admin or moderator action)

**v1-preview**: transitions happen in Django admin. Add an `ObservationAdmin` with list filtering by state and bulk "publish" action.

**Content flagging** (§13.4): flag icon on each photo and on the description text. `POST /flag/` creates a `ModerationFlag` record (model defined in Phase 1). Admins see flags in admin.

**User-facing copy**: never expose state names. "Your observation will be visible to others once it has been reviewed" etc.

---

## v1-preview complete

Before opening to test users:

- [ ] All user-facing strings have French translations (`compilemessages` passes)
- [ ] Cloudflare in front of the deployment — §16 names it the first line of bot defence and §11.1/§11.2 assume edge geo-tag data is captured on every request from day one; v1-preview is a "limited public preview" per §22.1, not an unexposed internal build, so this shouldn't wait for public launch
- [ ] Rate limiting on auth and submission endpoints (Django middleware or Cloudflare rules)
- [ ] Honeypot fields on all forms
- [ ] Email sending confirmed working end-to-end
- [ ] Photo upload to S3 confirmed working
- [ ] Mobile QA: iPhone Safari, Android Chrome, Android Firefox
- [ ] Admin can view and moderate all observations
- [ ] No observations from unvalidated users appear in the public map or GeoJSON endpoint

---

## Phase 9 — Password authentication (v1)

(§5.2) After magic-link is working, password auth is additive.

- Add `password_set` bool to `MobilitoUser` (shadow flag; the actual password hash is already in `AbstractBaseUser`).
- Auth flow: on email submission, if `password_set`, render password field alongside the magic-link option.
- Password set/change in user preferences (`/preferences/`).
- Standard Django `PasswordResetView` for forgot-password.
- OAuth2: evaluate at this point. `allauth` or `social-django` can be bolted on; only add if implementation time is ≤ 1 day.

---

## Phase 10 — Me-too and observation actions (v1)

(§9.2, §20.8)

- **Me-too**: `POST /observations/<id>/metoo/` — create `ObservationAction(type='me-too')`. HTMX replaces the button and count in-place.
- **Me-too cancellation**: tapping again within the cancellation window (`METOO_CANCEL_WINDOW_HOURS`, configurable) sets `cancelled_at`.
- **Unauthenticated me-too**: show email prompt modal; create provisional me-too; confirm on validation.
- **Observation updates** (progress / degradation / resolved): form on observation detail page; creates `ObservationAction` with optional text and photo.
- **Resolution display**: count `ObservationAction(type='resolved')` records; display "N users have noted this resolved" on the observation.
- **Location subscription**: me-too-ing an observation subscribes the user to updates on that observation (stored as a flag on `ObservationAction`).

---

## Phase 11 — Notifications (v1)

(§12)

- Add `notification_frequency` and `metoo_notification` preference fields to `MobilitoUser`.
- Post-observation email: send after observation transitions to Published. Include link to observation and current notification preference with one-click change link.
- Me-too notifications: exponential backoff (notify when cumulative me-toos exceed K × count at last notification; `METOO_NOTIFY_K = 1.8`, configurable).
- **Authority contact routing**: `ContactMethod` records (Phase 1 model). After observation is published, check whether observation's location falls within a known authority zone. If so and do-not-contact is False, cc the authority on the confirmation email. Requires PostGIS `ST_Within` query against authority zone geometry (add a `zone` geometry field to `ContactMethod`).
- Authority cc checkbox on the infrastructure observation submission form (§9.2, §12.2). Auto-checked if an authority is found; never hidden.

Email sending should move to async (Celery) before this phase if volume warrants it.

---

## Phase 12 — LLM features (v1)

(§9.2 step 5, §13.1)

**Infrastructure:** add Celery + RabbitMQ to docker-compose (see decisions table). RabbitMQ push-based delivery minimises task pickup latency, which matters here because the user is waiting on the page.

- **Tag proposals**: on observation submission, enqueue a Celery task that sends the description + photos to the Claude API. Response: ordered list of matching tag IDs with confidence scores. Return via HTMX poll (`hx-trigger="every 2s"` on the tag-selection form) until the task completes; render proposed tags sorted by confidence. User accepts, rejects, or adds more.
- **LLM content moderation**: enqueue on submission. Assess against the categories in §13.1. On result: update `moderation_state` accordingly. Clearly-illegal content: auto-sandbox + admin alert. PII flags: hold for human review. All content retained with audit trail regardless of outcome.

Use prompt caching where possible (system prompt + tag list as cached prefix).

---

## Phase 13 — Moderation dashboard (v1)

(§13.3)

- Add `is_moderator` flag to `MobilitoUser` (admins can grant it).
- `/moderation/` — moderator-only queue: list of observations in PendingModeration or flagged states, oldest first. Shows: content, tags, flag reasons, user validation state, time since submission.
- Actions: Publish / Light hold / Sandbox / Dismiss flags.
- Bulk actions for efficiency.
- This can be a Django admin customisation rather than a bespoke UI to keep scope manageable.

---

## Phase 14 — Engagement features (v1)

(§15.1)

- **Personal stats** (`/observations/mine/`): total observations, total me-too endorsements received, total views. Simple counters; add `view_count` to observations, incremented on each `GET /observations/<id>/`.
- **Community stats widget**: aggregate query (`COUNT` + `SUM` over recent published sessions) cached at 10-minute intervals. Rendered on the home page and observation summary pages.
- **Social media share**: Open Graph meta tags on observation detail pages; standard share buttons (Web Share API with fallback links) (§15.1).
- **Haptic + audio feedback preferences** (§17.2): preference fields on `MobilitoUser`; JS on the counting page reads them from a `data-` attribute on the page body. `navigator.vibrate()` for haptic; `SpeechSynthesisUtterance` for audio.

---

## Phase 15 — GDPR and public-launch requirements (v1)

(§5.5, §19.3)

- **User deletion flow**: view at `/preferences/delete/`. Replaces email with `deleted-user-<id>@example.com`, sets `is_active = False`, nulls all FK references to the user in `ObservationAction`, `InfrastructureMedia`. All observation content retained, fully anonymised.
- **Privacy policy**: static pages in FR and EN at `/legal/privacy/`.
- **Data retention**: management command to delete unvalidated users who have never submitted and are older than a configurable threshold (e.g. 90 days).
- **ROPA** (Art. 30 record of processing): internal document; not a code task, but required before launch.
- Legal review of the lawful basis table from §19.1.

---

## v1 complete

Before public launch, in addition to the v1-preview checklist:

- [ ] User deletion workflow operational
- [ ] Privacy policy live in FR and EN
- [ ] Authority contact routing tested for at least Nantes Métropole
- [ ] LLM moderation running in production (or human-moderation-only mode is a conscious fallback decision)
- [ ] Notification emails confirmed working including authority cc
- [ ] GDPR legal review sign-off

---

## Deferred to v2+

Per `design.md` §22.3: video, authority-as-user ticketing model, leaderboards/streaks/area challenges, SVM contact routing, automated moderation assignment, complex geographic ontology scoping.
