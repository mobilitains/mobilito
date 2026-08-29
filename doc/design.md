# Mobilito — Product Specification

*Derived from design.txt, design_claude.md, and design_codex.md — 2026-04-28*

---

## Lexicon

The following terms are used throughout this document with the meanings given here. This lexicon is the basis for user-facing terminology and will be extended to cover French equivalents when finalising copy.

| Term | Definition |
|---|---|
| **User** | A human using Mobilito. The UI says "you" and avoids the word "account". |
| **Visitor** | An unauthenticated user. |
| **Location** | A geographic point where an observation was made. Preferred over "site" to avoid confusion with "website". |
| **Observation** | A discrete data record contributed by a user — either a modal share session or an infrastructure report. |
| **Modal share session** | A time-bounded counting session in which the user tallies passing vehicles by transport mode. |
| **Infrastructure report** | A record describing the quality (positive or negative) of active mobility infrastructure at a location. In French: **signalement d'aménagement**. |
| **Aménagement** | The French term used in the UI for infrastructure. English copy uses "infrastructure". |
| **Tag** | A structured keyword from the infrastructure ontology applied to an infrastructure report. |
| **Me too** | An endorsement of an existing infrastructure report, indicating the endorser has observed the same condition. |
| **Validation** | Proving control of an authentication method (e.g. clicking a confirmation link). An observation is not publicly visible until its submitter is validated. |
| **Moderation** | Review of observation content for appropriateness and relevance. |
| **Publication state** | Whether an observation is visible to users other than its author and admins. |
| **Authority** | A government body, agency, or official with responsibility for active mobility infrastructure in a geographic zone. |

---

## 1. Product Overview

Mobilito is a Django web application for crowd-sourcing data on active mobility. It serves two purposes:

1. **Modal share counting** — recording how many pedestrians, cyclists, cars, and public-transport vehicles pass a given point.
2. **Infrastructure quality reporting** — documenting obstacles, hazards, and positive features of walking and cycling infrastructure, with photo evidence and keyword tagging.

An important goal of Mobilito is to enable mobility advocates to durably document their environment for planners and elected officials, and to render individual and aggregate observations transparent for journalists and other data consumers. The tool is a means of combatting motonormativity.

---

## 2. Domain Definitions

**Active mobility** means walking and cycling.

| Category | Includes |
|---|---|
| Walking ("ped") | Pedestrians, wheelchair users, people walking bicycles |
| Cycling ("bike") | Pedal bicycles, electric-assist bicycles, cargo bikes, unicycles, monowheels, skateboards, rollerblades |
| Cars | Cars, light trucks, heavy lorries, motorcycles, mopeds, electric scooters, and all other motor vehicles whether thermal or electric |
| TC (Transport Collectif) | Bus, tram, passenger boat, train |

Modal share sessions count **vehicles, not occupants**. This differs from the strict definition of modal share, which counts people. Help documentation must make this clear.

---

## 3. Product Goals

### 3.1 Primary goals

- Make it fast and pleasant for people to submit field observations from a phone.
- Build a useful public dataset about active mobility use and infrastructure quality.
- Let people discover observations near them.
- Encourage repeat participation without increasing submission friction.
- Preserve a lightweight identity model while limiting spam, abuse, and bot activity.

### 3.2 Secondary goals

- Help users forward infrastructure observations to the appropriate local authority.
- Enable gradual enrichment of infrastructure reports via follow-up contributions (me too, updates, resolution notes).
- Support bilingual usage from day one in French and English.

---

## 4. Product Principles

- **Mobile first.** Core workflows are designed primarily for phone usage in the field.
- **Low-friction identity.** Avoid "account" terminology in user-facing language. Use user-centric language throughout.
- **Pseudonymous, not anonymous.** The public does not see legal identity, but the system maintains a link to a user-controlled authentication method to manage abuse and observation ownership.
- **Location-aware only when needed.** Observation workflows collect location data; general browsing does not require it.
- **Progressive trust.** Allow a user to start quickly; require validation before making observations visible to others.
- **Structured plus flexible data.** Use a controlled ontology where possible, but allow free text and support ontology evolution.

---

## 5. Users & Identity

### 5.1 Identity model

- All users are **publicly pseudonymous**: no name, username, or profile is exposed to other users.
- An **email address is required** to record observations, linking them to a persistent (if pseudonymous) identity and deterring abuse.
- The word "account" is never used in the UI.

### 5.2 Authentication methods

| Method | v1 status |
|---|---|
| Email magic link (passwordless) | Required — preferred first-time path |
| Email + password | Required |
| OAuth2 (Google, Facebook, etc.) | Include if implementation cost is low; otherwise defer to post-v1 |

Magic-link login is the preferred first-time path: it is lower friction and doubles as email validation.

We'll (probably) implement password in the user preferences / profile section.  When the user provides an email address at auth time, we'll look up if they have a password and, if so, offer a password form as well as the magic link.

### 5.3 Authentication states

| State | Description |
|---|---|
| Unauthenticated | No identity known. Can browse; cannot submit observations. |
| Authenticated, unvalidated | Email known but not yet confirmed. Can submit; observations hidden from others and excluded from aggregates. |
| Authenticated, validated | Email confirmed. Observations are eligible for publication after moderation. |

### 5.4 Identity validation

- A user is **unvalidated** until they prove control of their auth method.
- Observations from unvalidated users are recorded but **not visible to other users, and excluded from aggregate statistics and map data**. Only the author and admins can see them.
- The user sees a plain note that their observation is not yet visible to others.
- Admins see a prominent banner showing: time since first interaction, number of observations, and a brief summary of when they were submitted.
- Validation can be deferred: the user can complete an observation session first and validate via the confirmation email afterwards. If validation is never completed, the observation is retained but remains permanently hidden.

### 5.5 User deletion (GDPR right to erasure)

- A user may request deletion of their identity at any time.
- Deletion replaces their email address with `deleted-user-<db_id>@example.com` and flags the user inactive (no further email). The db index id ensures uniqueness without leaking the original email; it is not exposed publicly.
- All observation data is retained but becomes fully anonymous — no record links it to the deleted identity.
- The deletion workflow is required before public launch but not in v1-preview.

---

## 6. Intended Audiences

Mobilito must serve two overlapping audiences:

- **Contributors:** people who submit modal share counts or infrastructure observations.
- **Readers:** people who browse, inspect, and reuse nearby observations.

Some readers will later become contributors; unauthenticated browsing must therefore be easy and low-friction.

---

## 7. Internationalisation (i18n)

- Initial languages: **French** and **English**.
- Language selection logic:
  1. If the user has a stored language preference, use it.
  2. Otherwise, auto-detect from the `Accept-Language` browser header.
- Users can switch language at any time; the choice is persisted server-side.
- All user-visible strings must be translatable from the start.
- Ontology labels and moderation copy must support language-specific variants.

---

## 8. Platform Requirements

### 8.1 Target environment

- **Mobile-first.** The primary use case is a user standing in the street with a phone.
- Must function well on common Android and iOS browsers, including Firefox.
- Core flows (browsing, reading observations) must work without JavaScript where feasible. Observation recording (real-time counting, map interaction) requires JavaScript.
- Linux hosted; Django backend.

### 8.2 Frontend stack

**Recommended: Django server-side rendering + Bootstrap 5 + HTMX + explicit JavaScript modules for a few well-defined components.**

**Layering policy**, most-preferred option first:

1. **Ordinary Django views and templates** as the baseline for every page.
2. **HTMX** for server-backed interactions (partial updates, polling, form submission without full reloads), rendered as server-side HTML fragments — not client-side templating over a JSON payload.
3. **Real links and forms wherever feasible**, so important workflows still function without HTMX. This is progressive enhancement, consistent with §8.1's requirement that browsing and reading work without JavaScript.
4. **Explicit JavaScript modules** for the few areas that genuinely need direct browser APIs: the map (Leaflet, geolocation, crosshair/pin interaction), the camera/photo flow (preview, multi-file handling), and offline resilience (tap-event queueing, sync-on-reconnect, draft persistence). These stay narrow and named — not a general-purpose client framework.
5. **A JSON API only where a component genuinely exchanges data rather than rendered UI** — e.g. the observations GeoJSON endpoint consumed by the Leaflet map, and modal-share tap events (§20.4). Anywhere the response is meant to be displayed, prefer an HTMX-rendered HTML fragment over a JSON payload plus client-side DOM manipulation.

- **Bootstrap 5** provides a mature, well-known component library with strong mobile support, responsive grids, and large tap targets out of the box. It is familiar to most Django developers and avoids framework lock-in.
- **HTMX version:** pin to **htmx 2.x** (the current stable/"latest" release line) for the initial build-out. htmx 4.0 was released 2026-08-28 as a parallel "next" line (XHR replaced by fetch, attribute inheritance explicit by default, some event names renamed); the htmx project does not expect 2.x to be superseded as "latest" before roughly early 2027. Re-evaluate at Phase 4 of the roadmap (Map component — the first phase that actually wires up HTMX): since no HTMX code exists yet, there is nothing to migrate, and if 4.0 has a stable track record by then, adopt it directly instead of starting on 2.x. The htmx project characterises the 2→4 behavioural differences as small, so deferring the choice costs little either way — the only real risk today is building on a release that is one day old.

A full SPA framework (React, Vue, etc.) is not warranted: the interactive surface is narrow, and the overhead in tooling, build pipeline, and developer specialisation outweighs the benefit. HTMX satisfies the AJAX requirements; jQuery is not needed.

---

## 9. Core Features

### 9.1 Modal share counting

**Purpose:** Count how many individuals of each transport mode pass a fixed observation point over a time window.

**Modes tracked:** pedestrian, cyclist, car, TC.

#### UI

- Four large square buttons filling most of the bottom of the screen, one per mode.
- Each button shows a pictogram of the mode and a running count (smaller text).
- Every tap increments the count for that mode and records a **timestamp**. Events are sent individually; at session end the client also sends final mode totals. Storing both permits integrity cross-checking.
- A **Finish** button is displayed prominently at the top.

#### On finish

- Display results graphically (e.g. bar chart of mode counts).
- Email the user a link to their observation (per their notification preference — see §12.1).
- If unvalidated, prompt gently (without "account") to confirm identity via the confirmation email.

#### Minimum session duration

A configurable minimum session duration is enforced. If a user finishes before the minimum, the UI asks: *"That was a short session — keep it or discard it?"* The threshold may be a named constant in the code, we don't anticipate changing it often.

#### "Do another count here"

On the observation summary page, a button offers to start a new session at the same location. This is the modal share equivalent of the infrastructure "me too" — encouraging repeat observations rather than endorsing a previous count.

There is no me-too for modal share: each count is independent and must be made in the field.

#### Time evolution

The system must support visualising how modal share counts at a given location evolve over time. Multiple sessions at the same location are ideally linked through the location model, though note that two users who record at different points on the same street should leave a record of having been at different points, even if we aggregate them together because there in some sense on the same street section.

#### Presence

If the device GPS is far from the stated observation location, the observation is internally flagged as lower reliability and noted publicly. The observation is not rejected.

### 9.2 Infrastructure quality reporting

In French: **signalement d'aménagement**.

**Purpose:** Document a specific location's active mobility infrastructure as an obstacle, hazard, or positive feature.

Physical presence is not required: for example, a me-too from home about a daily commute condition is valid.

#### Map interaction

The map uses the **crosshair/centre-of-map pattern**:

- A fixed crosshair is overlaid at the centre of the screen. The reported observation location is always the crosshair = the map centre.
- To set location: pan the map until the crosshair is over the correct spot, then tap **Confirm location**. No long-press, no separate mode.
- Existing observation pins are distinct coloured markers. Tapping a pin opens a **bottom sheet** (a panel sliding up from the bottom of the screen, leaving the map visible behind it) showing the observation summary. This is handled by the pin's touch target, not the map canvas, so it does not conflict with panning or crosshair positioning.
- A floating bullseye/target icon anchored to the top-right of the map snaps back to the device's current GPS position.

#### Selecting an existing observation

The bottom sheet offers:

- **Me too** — endorses the observation, increments a publicly visible counter, and optionally subscribes the user to future updates on this observation. The me-too appears in the user's own observation list. Me too can be cancelled within a configurable time window (several hours) by tapping again. If the user is unauthenticated, a modal collects their email and me-too is confirmed on validation.
- **See more** — full detail: photos, text, tags, history.
- **Add an update** — progress, further degradation, or resolution.
- **Add a related observation** — a new, separate observation at the same location (e.g. the original report was about a lamppost; the user adds a report about a dangerous kerb).

#### Observation resolution

There are no formal states managed by authorities. Instead:

- If multiple users note an observation as resolved, the system displays: *"N users have noted this resolved."*
- If the original submitter or a known authority representative notes resolution, this is highlighted: e.g. *"Nantes Métropole Pôle Centralité says this is resolved; 2 users since report it is not."*
- Degree-of-certainty indicators (based on observer activity and dispute history) are deferred to post-v1.

#### Creating a new observation

1. User confirms or adjusts their crosshair location.
2. Selects observer perspective: pedestrian / cyclist / both (same square button style as modal share, but selecting role, not counting).
3. Uploads one or more photos (required in v1; video is deferred to v2).
4. Adds a free-text description.
5. System sends photos and text to an LLM asynchronously and proposes matching ontology tags in most-likely-first order.
6. User accepts, rejects, or supplements proposed tags.
7. User submits. Observation enters the publication lifecycle (§14).

Photo EXIF geodata is checked and stored where present, as corroboration of the stated location. Missing EXIF geo is acceptable and does not block submission.

#### Editing and deletion

Users cannot edit or delete their own observations after submission. Updates and corrections are handled through the observation update / me-too mechanism.

#### Flag icon

All user-supplied content (photos, text) carries a flag icon for any user to report inappropriate content. Flagging adds to the moderation queue. Repeated flagging of a single item may trigger auto-unpublishing; precise thresholds are configurable by admins.

#### Authority notification

If the observation falls within a zone for which we have mailable authorities on file, include a checkbox just before the finish button (auto-checked) saying "send a copy (cc:) of my observation to ...".  We must respect a user's wish not to auto-notify the authority.  More importantly, we must not surprise a user by cc'ing the authority.


### 9.3 Browsing without observing

- Any user (authenticated or not) can browse existing observations on a map.
- Observations near the user's location are surfaced by default.
- Browsing never requires geolocation permission.

---

## 10. Infrastructure Ontology

- Stored in the database, not hard-coded. Admins can add and modify tags without code changes.
- Minimum schema: tag name, description, country (null = applies everywhere).
- **Geo-dependent applicability:** tags can be scoped to a jurisdiction.
  - Example: Nantes Métropole's RAC reference book — tags may reference RAC conformity.
  - Example: France-specific tags may reference French legislation.
- Tags do not have different *meanings* in different places; jurisdiction scoping governs *applicability*, not meaning.
- For v1: a core shared tag set plus optional local extensions for Nantes Métropole. This validates the ontology schema without a full rules engine. Further tag additions are made by admins, not developers.
- More complex geographic scoping (e.g. "valid in these three regions but not those four") is deferred.

---

## 11. Geolocation & Location Data

### 11.1 What is recorded per observation

- GPS coordinates reported by the device.
- User-adjusted coordinates (if the user moved the crosshair).
- Image EXIF geolocation (extracted from uploaded photos where present).
- Cloudflare HTTP geo-tag data (country, region, etc.) on every request during the observation session.
- Any other available network-edge metadata.

Reverse geocoding converts coordinates to a human-readable address. The user can edit this address. If the location has no street address (park, open space), no address is required — the user is encouraged to give the spot a name.

### 11.2 User preference and browser permission

**User preference checkbox**

- The observation UI and user settings expose a checkbox labelled *"Use device location"* / *"Utiliser la position de l'appareil"*.
- **Checked by default.** This is a preference controlling a feature, not a GDPR consent mechanism — actual consent is the browser permission prompt. The framing must be explicit: this is "use device location for this observation", not "I consent to data collection".
- State is stored server-side and persists across devices and sessions.
- When unchecked, the app never calls the browser geolocation API; no browser permission prompt appears.

**Browser geolocation permission**

- The browser's native permission prompt fires at most once per origin. After granting, the browser remembers silently — no repeated prompts on subsequent sessions.
- The app requests permission only when the user starts an observation session and the preference checkbox is checked.
- If permission is denied, the user places the crosshair manually; the observation is flagged as location-unverified.

**Trade-off communication**

When the checkbox is unchecked or browser permission denied, the UI states plainly: *"Your observation will be marked as location-unverified, which makes it less useful to planners."* No legalese.

Cloudflare geo-tag data is recorded on every observation request regardless, as a secondary signal requiring no user-facing permission.

### 11.3 For modal share vs. infrastructure

For modal share, the map is displayed only at session start (and when viewing past sessions later). For infrastructure, the map is central to the observation definition and is not recorded as final until the full observation is submitted — though partial state may be persisted in the background to support recovery after connectivity loss (or, more realistically, if the user is distracted and then comes back to the same spot later and starts an observation, we might say "do you want to continue your observation from <date>?".

### 11.4 Location equivalence

Two observations are "at the same location" if within a configurable radius. The appropriate radius differs by observation type:

- **Infrastructure:** small (~2–3 m) so that conditions on opposite sides of the street are not conflated.
- **Modal share:** a stretch of path or road without branch points — any point on the stretch is equivalent, since a traveller passing one point will pass all others.

These thresholds are stored in configuration, not hard-coded, to allow adjustment based on operational experience.

---

## 12. Notification & Contact Methods

### 12.1 User notifications

After a completed, validated observation, the system emails the user a link to their observation.

**Email frequency (per user):**

| Option | Default? |
|---|---|
| Every observation | Yes (default) |
| Batch — one email every N days (configurable, default N = 1) | No |
| Never | No |

Confirmation emails include the user's current notification preference and a link to change it.

Also provide a preference for notifying about me-too's for observations that you watch (are alerted about changes about).

| Option | Default? |
|---|---|
| Every me-too | No |
| Exponential backoff | Yes |
| Never | No |

Exponential backoff notifies the user of a me-too if the cumulative number of me-too's exceeds K times the number at last notification for that user.  The default value of K = 1.8.


### 12.2 Authority contact methods

- The system maintains a database of contact email addresses for geographic zones.
- Some zones may have no contact information; some may have overlapping or unclear responsibility (multiple contacts).
- Contact records carry a **"do not contact directly"** flag. When set:
  - The system does not email that address.
  - The user's confirmation email may include: *"You can forward this to [authority] at [address], but they have asked us not to contact them directly."*
- When emailing a validated user their observation, the platform cc's the appropriate authority contact if one is known and not opted out. Authority email routing is a v1 feature, not a v1-preview requirement.

**Zone-to-contact mapping for v1:**

- For clear administrative boundaries (département), use a manually curated lookup table.
- For complex zones (e.g. Nantes Métropole pôles de proximité, whose boundaries are not machine-readable), also use a manual lookup table based on best available knowledge. An SVM or other classifier is a possible later enhancement; it is deferred from v1.
- Rules for determining authority responsibility in ambiguous cases are also deferred; the data model accommodates them.

**Authority accounts — future direction:**

Modeling authority contacts as platform users with special permissions — enabling a ticketing-like workflow where `infra@commune.org` can delegate to `user1@commune.org` as an authorised responder — has real merit: unified identity model, in-platform responses, richer audit trail, and reason for authorities to engage. However, this is significant scope and is deferred to v2. For v1, contact methods are simple records (email, geography scope, do-not-contact flag). The data model includes an optional `user_id` link so this evolution does not require a schema migration.

---

## 13. Content Moderation

### 13.1 LLM-assisted moderation

LLM moderation runs asynchronously after submission. It assesses content against actionable categories:

| Category | Action |
|---|---|
| Clearly illegal content (CSAM, illegal weapons, etc.) | Immediate removal; flag for admin |
| Personally identifying information (faces, licence plates) | Flag for potential redaction; do not auto-publish |
| Spam or commercial content | Flag for human review |
| Harassing or abusive content | Flag for human review |
| Off-topic content (unrelated to active mobility) | Flag for human review |
| Potentially misleading infrastructure claims | Flag for human review; do not auto-remove |
| Charged language that could be toned down | Suggest rewording to the submitter |

Moderation flags do not destroy user data. All content is retained with an audit trail regardless of outcome.

### 13.2 Moderation levels

- **Light hold:** observation excluded from browsable/searchable results but accessible by direct URL, with a note that it is under review.
- **Sandbox (heavy):** inappropriate content — visible only to the author and admins.

### 13.3 Moderation roles and workflow

- Users can be designated as **moderators** by admins; moderators access the moderation dashboard.
- Eventually, moderation may be auto-assigned based on reliability (observation frequency, peer-review agreement). This is deferred.
- No SLA. The dashboard shows the queue; moderators act as capacity allows.
- A moderation dashboard is required for v1 but not for v1-preview.

### 13.4 User-initiated flagging

Any user can flag any user-supplied content (photo, text) as inappropriate. Flagging adds to the moderation queue. Repeated flagging of the same item may trigger automatic light hold or sandboxing; thresholds are configurable by admins.

---

## 14. Observation Lifecycle

Observations pass through internal publication states:

| State | Description | Visible to others? |
|---|---|---|
| Draft | In progress, not yet submitted | No |
| Submitted | Received by the server | No |
| Pending validation | Waiting for user to validate auth method | No |
| Pending moderation | Queued for moderation review | No |
| Published | Cleared for public view | Yes |
| Light hold | Accessible by direct URL; not in search | Partially |
| Sandboxed | Inappropriate content; author and admin only | No |

These state names are for developer and admin use. Users see plain language: "your observation is not yet visible to others" rather than state labels.

---

## 15. Engagement & Retention

Goal: make Mobilito *moderately addictive* without compromising the mission or the pseudonymous identity model.

### 15.1 For v1

- **Personal stats:** show the user how many observations they have made, how many me-too endorsements their observations have received, and how many times their observations have been viewed. Encourages continued contribution without competitive pressure.
- **Community stats widget:** display aggregate numbers (*"This week, Mobilito users counted X cyclists in Nantes."*) on the home screen and on observation summaries. Makes individual observers feel part of something larger. Low complexity, on-mission, non-competitive.
- **Social media share:** make it easy for anyone to share any observation on social media using standard and conventional widgets

Both are required for v1; not required for v1-preview.

### 15.2 Engagement mechanics — proposals for post-v1

| Mechanic | Pros | Cons | Verdict |
|---|---|---|---|
| Streaks | Strong engagement driver; encourages regularity | Creates pressure; fails users who cannot observe regularly; can feel gamey | Defer to v2; offer easy reset if implemented |
| Leaderboards | Motivates competitive observers | Conflicts with pseudonymous ethos; rewards quantity over quality | Defer; pseudonymity makes it difficult to implement well |
| Badges / achievements | Milestone recognition; low friction | Novelty fades quickly; mild long-term effect | Include in v2, starting with simple milestone badges |
| Progress bars | Visual sense of accomplishment | Low long-term engagement | Include in v1 stats dashboard (simple bar) |
| Location subscription | Notify user when a location they've contributed to gets an update | High value for repeat contributors; low friction | Included in v1 via me-too subscription model |
| Geographic coverage challenges | Encourage observations in new areas | Complexity; needs enough users to be meaningful | Defer |

---

## 16. Bot Protection

Multiple complementary layers:

| Mechanism | Efficacy | Dev load | Operational load | Decision |
|---|---|---|---|---|
| **Cloudflare** (IP reputation, rate limiting, bot fingerprinting, challenge pages) | High — handles the majority of automated traffic | Very low — already part of the infrastructure | Low — Cloudflare manages it | **Include in v1. First line of defence.** |
| **JavaScript requirement** | Moderate — many simple bots do not execute JS | Zero — observations already require JS | Zero | **Implicit in v1; no extra work needed.** |
| **Email validation gate** | High — ensures a valid email before observation is published | Low — already planned | Low | **Include in v1. Primary identity gate.** |
| **Honeypot fields** | Moderate — catches naive bots that fill all form fields | Very low — one hidden field per form | Zero | **Include in v1.** |
| **Rate limiting** (server-side, per user and per IP) | High against mass-submission bots | Low — Django middleware or Cloudflare rules | Low | **Include in v1.** |
| **CAPTCHA** | High — last resort | Low dev effort; high user friction | Low | **Defer. Trigger only on strong anomaly signals; never on a first visit.** |

The combination of Cloudflare, JavaScript requirement, email validation, honeypot fields, and rate limiting is expected to handle the realistic threat level for an early-stage civic data app without exposing legitimate users to CAPTCHA friction.

---

## 17. Accessibility & Usability

### 17.1 Tap targets and one-handed use

- All counting buttons must be usable one-handed on mobile.
- Button targets are large and visually distinct, reachable by the thumb of either hand.

### 17.2 Haptic and audio feedback (user preferences)

- **Haptic feedback on tap:** available as a user preference (default: off, because vibration is intrusive in many contexts).
- **Audio feedback on tap:** the app can optionally speak the mode name (localised: "vélo", "piéton", "voiture", "transport") on each tap. Available as a user preference (default: off).
- Both preferences are persisted server-side.
- Both are nice-to-have for v1 but may be implemented just after if necessary.

### 17.3 Undo

Where feasible:

- **Me too:** cancellable within a configurable time window (several hours) by tapping again.
- **Accidental taps during modal share counting:** all counts are noisy, encourage repeat counting over time rather than perfection each time.

---

## 18. Offline Resilience

4G coverage is excellent through most of Europe, so the app assumes basic connectivity. Resilience strategy:

- **Modal share tap events** are queued client-side if immediate send fails, then submitted in batch.  We'll need to assure that timestamps remain correct and not the batch time.  A short network dropout during counting does not lose data.
- If connectivity is lost entirely during a modal share session, the client retains the session locally and syncs when connectivity returns.  Reminder not to close the page until sync'd.
- Longer outages are handled on a best-effort basis: the app warns the user, retains local data, and syncs when possible.  Reminder not to close the page until sync'd.
- The infrastructure observation form may persist partially completed content client-side as a draft, allowing recovery after a connectivity loss.

---

## 19. Privacy & GDPR

### 19.1 Data categories and lawful basis

| Data | Basis | Notes |
|---|---|---|
| Email address | Contract (providing the service) | Required to link observations to a persistent identity |
| OAuth2 tokens | Contract | Optional, if OAuth2 is supported |
| Observation content (text, photos) | Legitimate interest | Data about infrastructure in the physical world, not about the user |
| Observation location | Legitimate interest | User explicitly states they were/are at that location |
| Device GPS coordinates | Legitimate interest | Corroborates stated location; not PII |
| Cloudflare geo-IP | Legitimate interest | Secondary location signal; standard practice |
| Image EXIF geodata | Legitimate interest | Corroborates stated location |

### 19.2 Observation data is not personal data

Photos and text in observations describe physical-world infrastructure. The user's act of submitting is itself a disclosure that they were (or have been) at that location — this is the user's deliberate choice, not incidental capture. Location data attached to observations therefore describes infrastructure, not the user. Geo data also serves fraud and spam detection (device GPS far from stated location) and data reliability assessment.

### 19.3 GDPR requirements before public launch

- Formal privacy policy in French and English.
- Data retention periods and automated deletion schedule.
- Record of processing activities (ROPA, GDPR Art. 30).
- Legal review of the lawful basis for each data category above.
- The right-to-erasure workflow (§5.5) must be operational.

---

## 20. Data Model Sketch

This is not a final schema. It identifies the main entities and relationships.

### 20.1 User

- Internal id
- Auth methods (email + hash, OAuth tokens)
- Validation state
- Preferred language
- Notification frequency preference
- Haptic / audio feedback preferences
- Bot-risk / trust signals
- Moderation history
- Deleted flag + anonymised email (for deleted users)

### 20.2 Location

- Internal id
- Canonical geographic point (PostGIS geometry; full precision)
- User-entered address
- Reverse-geocoded address
- Area metadata (country, département, commune, etc.)

Location equivalence radii are stored in configuration, not hard-coded (see §11.4).

### 20.3 Modal share session

- Internal id
- User id
- Location id
- Started at / finished at
- Publication state
- Validation state
- Count totals by mode (stored at session end, for integrity cross-check against event stream)
- Session integrity hash (computed from event stream; detects truncated sessions)

### 20.4 Modal share count event

- Session id
- Timestamp
- Mode (ped / bike / car / TC)
- Latitude, longitude at time of tap (if geolocation is active)

### 20.5 Infrastructure observation

- Internal id
- User id
- Location id
- Observer perspective (ped / bike / both)
- Description (free text)
- Publication state
- Moderation state
- Created at / updated at

### 20.6 Infrastructure media

- Internal id
- Observation id
- Added-by user id
- Date added
- Media type
- Storage reference (S3-compatible; see §20.11)
- EXIF geolocation (if present)
- Moderation state
- Published flag (default true unless flagged)

### 20.7 Infrastructure tag

- Internal id
- Label (per locale)
- Description (per locale)
- Geography scope (country; null = universal)
- Ontology family / category
- Status (active / deprecated)

### 20.8 Observation action (me too, update, resolution)

- Internal id
- Parent observation id
- Action type: me-too / progress / degradation / resolved / additional-issue
- Text (optional)
- Media (optional)
- Created by user id
- Created at
- Cancelled at (for me-too cancellation)

### 20.9 Contact method

- Internal id
- Geography scope (country, département, commune, etc.)
- Contact type (email; extensible)
- Contact value
- Do-not-contact flag
- Display guidance (shown to user when do-not-contact is set)
- Future: optional user_id (for authority-as-user model, v2+)

### 20.10 Location evidence

- Internal id
- Observation id (modal share session or infrastructure observation)
- Device geolocation
- User-adjusted geolocation
- Edge geolocation (Cloudflare)
- EXIF geolocation
- Accuracy metadata
- Timestamp

### 20.11 Media storage

S3-compatible object storage (AWS S3 or Cloudflare R2; to be decided). All media storage references in the data model use an S3-compatible URL/key.

---

## 21. User Flows

### 21.1 Unauthenticated visitor

1. Landing page: brief pitch (a few sentences maximum); two prominent calls to action — *Browse observations* and *Make an observation* (final copy TBD; must be more compelling than these placeholders).
2. Browsing: fully open, no authentication required.
3. Attempting to make an observation: prompt for email or OAuth before proceeding. Mark as unvalidated. Allow observation to be completed; the validation prompt comes at the confirmation stage.

### 21.2 Authenticated user — home screen

- Two large side-by-side buttons at the top: *Count modal share* and *Report an aménagement* (final copy TBD; French: *Compter les modes* and *Signaler un aménagement*).
- Below: links to own past observations and to the public map/browse view.

### 21.3 Authenticated user — modal share (Scenario A/MS)

1. Show map centred on device location. Fixed crosshair marks the observation point. User pans to adjust; a bullseye button re-reads GPS.
2. Reverse-geocode pinned location; display editable address.
3. Display four counting buttons (ped / bike / car / TC).
4. User taps buttons; each tap is timestamped.
5. User taps **Finish** (minimum duration check applies).
6. Show graphical summary of counts.
7. Send confirmation email per notification preference.
8. If unvalidated, prompt for identity confirmation.

### 21.4 Authenticated user — infrastructure (Scenario A/I)

1. Show map with existing observation pins; crosshair marks the user's point.
2. User either:
   - **Selects an existing pin** → bottom sheet summary → me too / see more / add update / add related observation.
   - **Confirms crosshair location** → selects observer perspective → adds photos → adds text → reviews LLM-proposed tags → submits.
3. Confirmation email sent; authority cc'd if applicable and allowed.

---

## 22. Version Scope

### 22.1 v1-preview

Minimum viable product for internal testing and limited public preview:

- Email magic-link authentication
- Modal share counting (full workflow)
- Infrastructure observation (location, role selection, photo upload, free text, manual tag selection — no AI tagging yet)
- Map browsing of existing observations
- Basic moderation queue (admin-accessible; no public moderation dashboard)
- French and English UI

### 22.2 v1

Full public release, adding:

- Email + password authentication
- OAuth2 if feasible
- AI-assisted tag proposals and LLM moderation
- Me-too, observation update, and resolution flows
- Authority contact routing (manual lookup, limited geographic coverage)
- User notification frequency preferences
- Engagement: personal stats, community stats widget, location subscription
- Accessibility preferences (haptic, audio feedback)
- Moderation dashboard (moderator-role users)
- User deletion workflow (GDPR right to erasure)
- Admin-configurable ontology tags
- Success metrics dashboards (admin)

### 22.3 Deferred to v2 and beyond

| Feature | Notes |
|---|---|
| Video for modal share | Explicit v2 |
| Video for infrastructure reports | Explicit v2 |
| Authority-as-user (ticketing model) | v2+ direction; data model accommodates it |
| SVM contact-routing classifier | Defer; manual lookup table first |
| Complex geographic ontology scoping | Post-v1 |
| Degree-of-certainty indicators | Post-v1 |
| Leaderboards, streaks, area challenges | v2 with careful design |
| Automated moderation assignment | Post-v1 |
| Geographic restrictions | No restrictions; operationally focused on Nantes Métropole at launch |

---

## 23. Non-Functional Requirements

### 23.1 Mobile usability

- Core actions must work well on common smartphone screens.
- Counting interactions must feel responsive and reliable under poor field conditions.
- Large tap targets throughout.

### 23.2 Privacy

- Public display must not expose unnecessary personal data.
- Location collection is minimised outside observation workflows.
- Internal anti-abuse data is separated from public-facing observation data.

### 23.3 Abuse resistance

- Bot protection as specified in §16.
- Rate limiting and anomaly detection for suspicious observation patterns.
- Validation and moderation reduce drive-by spam without blocking legitimate users.

### 23.4 Extensibility

- The ontology system supports geography-specific rule sets.
- Contact routing allows replacement of simple lookup tables with more advanced classification later.
- Location equivalence thresholds are configurable.
- Minimum modal share session duration is configurable.
- Moderation thresholds (auto-unpublish trigger counts) are configurable.
