# TODO — Jeff

Things only you can do to get Mobilito to the v1 preview: accounts
and services to provision, decisions to make, and a test plan.
Code-side status is in [doc/roadmap.md](doc/roadmap.md); how-to
detail is in [doc/operations.md](doc/operations.md).

## 1. Decisions

- [ ] **Hosting.** Where does v1-preview run? (Production target
      in CLAUDE.md: Ubuntu 22.04, Python 3.10+.) A single small VM
      with PostGIS, gunicorn and nginx is enough for a preview.
      Once you've chosen, I can add the app server, static-file
      serving and systemd/cron config.
- [ ] **Domain name** for the preview (needed for Cloudflare, SES,
      `ALLOWED_HOSTS`, `DJANGO_SITE_URL`).
- [ ] **Who moderates** during the preview, and how often. Nothing
      is visible to others until someone presses "Publish" in the
      admin, so testers will wonder where their observations went
      if nobody does.
- [ ] **Contact address** to show people. Set it as
      `DJANGO_CONTACT_EMAIL` (shown to authors whose observation was
      hidden) and in `GEOCODING_USER_AGENT` (Nominatim requires
      one). No page shows a general contact yet.
- [ ] **Auto-hold threshold** for flags
      (`MODERATION_FLAG_AUTO_HOLD_REPORTERS`, default 3 confirmed
      people). Fine for a preview; revisit for launch.

## 2. Provisioning

- [ ] **Server/VM** with PostgreSQL 15 + PostGIS 3, GDAL, Python
      3.10+, gettext.
- [ ] **AWS SES**: verify the sending domain, set up SPF/DKIM/DMARC,
      request production access (out of the sandbox, or you can only
      mail verified addresses), create SMTP credentials. Set
      `EMAIL_HOST`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` and
      `DJANGO_DEFAULT_FROM_EMAIL`.
- [ ] **Cloudflare R2**: create a **private** bucket and an API
      token limited to it. Set `R2_BUCKET_NAME`, `R2_ENDPOINT_URL`,
      `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`.
- [ ] **Cloudflare** in front of the site: proxied DNS, TLS to the
      origin, the **"Add visitor location headers"** managed
      transform, and a few rate-limiting rules (`/auth/start/`,
      `/counts/start/`, `/reports/new/submit/`, `/flag/`). Then set
      `RATE_LIMIT_CLIENT_IP_HEADER = "HTTP_CF_CONNECTING_IP"`.
- [ ] **Shared cache** for several workers (the database cache is
      enough: `createcachetable`), or run one worker process.
- [ ] **Cron** for `manage.py process_sign_in_attempts` (hourly).
- [ ] **Backups** of the database and the R2 bucket.
- [ ] **Error reporting / uptime check**: even just email on 500s
      (`ADMINS` + SES) and an external uptime ping.
- [ ] Optional: a **Mapbox** token if Nominatim's 1 request/second
      limit becomes a problem.

## 3. Deployment concerns

- [ ] `DEBUG` is `True` in `settings.py` and there's no production
      settings file: production needs a `settings_local.py` like the
      one in operations.md ("Configuration"). Run
      `manage.py check --deploy` and clear what it reports.
- [ ] **`compilemessages` on every deploy.** `.mo` files aren't in
      git, and without them the site shows English only.
- [ ] `collectstatic`, and something to serve `staticfiles/`.
- [ ] Load the tag fixture **once** (`loaddata
      initial_infrastructure_tags`). Re-running it overwrites admin
      edits to those tags.
- [ ] Create your admin user, and tick "Email validated" on it.
- [ ] `npm audit` reports 1 high-severity vulnerability in the
      **dev-only** Jest dependencies (nothing ships to browsers from
      npm). Check it, and run `npm audit fix` if it's clean.
- [ ] The French translations were written by Claude. **Have a
      native speaker read them**, especially the home page, the
      counting guide and the emails (`locale/fr/LC_MESSAGES/django.po`).
- [ ] Tag families (walking, cycling…) show untranslated in the
      report form (roadmap Phase 6 follow-up). Decide whether that's
      acceptable for the preview.
- [ ] Times display in UTC (roadmap Phase 7 follow-up). A count at
      17:00 in Nantes reads 15:00 or 16:00. Probably worth fixing
      before testers see it; say if you want me to.

## 4. Test plan

### 4.1 Automated (every change)

`./docker/docker-manage.sh test`: lint, about 400 Django tests
(98% coverage) and about 120 JS tests. CI runs the Django tests on
Python 3.10 and 3.12.

### 4.2 Smoke test on the deployed preview (you, ~30 minutes)

Do these on a real phone, over mobile data, against the real
services:

1. **Sign in**: ask for a link, receive it (check spam), open it,
   and confirm you're signed in. Try "Stay signed in" both ways.
2. **Count**: start a count at your location, tap each mode, put
   the phone in airplane mode for a few taps, come back online and
   check that nothing was lost. Finish, and see the results.
3. **Count with a new email**: in a private window, start a count
   with a new address (provisional sign-in), then confirm from the
   email app. The count should now be yours.
4. **Report**: take 2–3 photos with the camera, add a description
   and tags, send. Check the photos in R2 (and that the bucket URL
   isn't public).
5. **Moderate**: in the admin, find both observations under
   "Pending moderation" and publish them. Check they appear on
   `/map/` and `/observations/` when signed out.
6. **Flag**: signed out, flag a photo and the description. Check the
   flags in the admin, mark them dealt with. Put the report on light
   hold: it should be off the map but open by its link, with a note.
   Sandbox it: 404 when signed out.
7. **Language**: switch FR/EN signed out and signed in. Check that
   the preference sticks after signing out and back in.
8. **Cron**: leave a provisional attempt unconfirmed. Check that the
   reminder email arrives after 24 h and the data is gone 7 days
   later (or shorten the settings on a test instance).
9. **Unvalidated invariant**: with an unconfirmed address, observe,
   then try to publish it in the admin. It must be refused.

### 4.3 Device matrix (roadmap checklist)

Run 4.2 steps 1, 2, 4 and 7 on each of: **iPhone Safari**,
**Android Chrome**, **Android Firefox**. Also check the map and the
counting screen on a small phone (≤ 360 px wide), in bright sun
(contrast), and one-handed.

### 4.4 Observed user testing

The design is built for untrained volunteers, so watch real people
use it. Proposal:

- **Who**: 5–6 people per round. Mix regular cyclists or
  pedestrians who've never counted traffic, one or two older or less
  tech-confident people, and at least one screen-reader or
  large-text user if you can. Five people find most of the big
  problems; do two rounds with fixes in between rather than one big
  one.
- **Where**: outdoors, at a real street in Nantes, on the tester's
  own phone. Counting in the street is the product. A café test
  won't show glare, noise or one-handed use.
- **How**: give a task, not instructions, then stay quiet. Ask them
  to think aloud. Note where they hesitate, what they tap that isn't
  a button, and what they say. Don't help unless they're stuck for
  more than a minute (and note that you did). About 30–40 minutes
  each. Record the screen if they agree (iOS/Android screen
  recording), not their face.
- **Tasks** (read them aloud):
  1. "You've heard about Mobilito. Open this link and find out what
     it's for." (Landing page: do they get it in 10 seconds?)
  2. "Count what goes past here for five minutes." (Sign-in with
     email, finding the spot, the four buttons, what counts as what:
     watch the edge cases, such as e-scooters, delivery bikes and
     buses. Then finishing.)
  3. "What did you find? How does it compare with earlier counts
     here?" (Results page and history.)
  4. "That cycle lane / crossing / obstacle over there — record it
     so the city could see it." (Map pin, photos, description,
     tags.)
  5. "Where are your observations now? Can other people see them?"
     (Your observations page and status wording. Do they understand
     "waiting to be checked"?)
  6. "Find what other people have recorded near here." (Map, list,
     bottom sheet.)
  7. "One of these photos shows someone's face. What would you do?"
     (Flagging: can they find it, and do they trust it?)
- **Afterwards** (5 minutes): "What was confusing? What would stop
  you using it again? Would you use it on your own?" Plus the
  System Usability Scale (10 questions) if you want a number to
  compare between rounds.
- **Record** each problem with how many testers hit it and how badly
  (blocked / slowed / annoyed). Fix the "blocked" ones before the
  next round.
- **Consent**: a short form saying what's recorded, how long it's
  kept and that they can stop at any time. Testers' observations
  are real data: tell them, or use a test instance.

### 4.5 Preview exit criteria (proposal)

- All of the roadmap's v1-preview checklist ticked.
- Most testers finish tasks 2 and 4 without help.
- No lost counts or photos over a week of real use.
- Moderation queue kept under, say, 48 h.
