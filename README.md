# Mobilito

Mobilito is a web app for crowd-sourcing data on active mobility:
walking, cycling and public transport. Volunteers use their phones
to record what actually happens on their streets, so that advocates,
planners, elected officials and journalists can see it.

It has two kinds of observation:

- **Modal share counts.** Stand at a spot and tap one of four big
  buttons (pedestrian, bike, car, public transport) each time
  something goes by. Mobilito timestamps every tap, works offline,
  and shows how the mix changes at that spot over time.
- **Infrastructure reports** (*signalements d'aménagement*). Put a
  pin on the map, add photos, a description and tags describing a
  cycle lane, crossing, obstacle or anything else worth recording.
  Good features are as welcome as problems.

Anyone can browse published observations on a map or in a list
without signing in. Observers sign in with an emailed link (no
password) and stay pseudonymous: Mobilito stores an email address
and nothing else about who you are. Nothing is shown to others until
its author has confirmed their email and the observation has been
reviewed.

Mobilito starts in Nantes, in French and English, and is run by
Français pour une Meilleure Mobilité.

## Status

In development, heading for a limited **v1 preview**. See
[doc/roadmap.md](doc/roadmap.md) for what's done and what's next.

## Tech

Django 5.2 with PostGIS, server-rendered pages with Bootstrap 5 and
htmx, Leaflet with OpenStreetMap tiles, and a little vanilla JS for
the map, the counting screen and offline queueing. Photos go to
S3-compatible storage (Cloudflare R2).

## Getting started

With Docker:

```bash
./docker/docker-manage.sh up      # http://localhost:8000
./docker/docker-manage.sh test    # lint + Django tests + JS tests
```

See [doc/operations.md](doc/operations.md) for more, including
running without Docker, and [doc/README.md](doc/README.md) for the
rest of the documentation.

## Contributing

Issues and pull requests are welcome. Please read
[CLAUDE.md](CLAUDE.md), which sets out the project's conventions
(tests, code style, licence headers, commits) for people and AI
assistants alike.

## Licence

[GNU Affero General Public License v3.0 or later](LICENSE).
Copyright © Français pour une Meilleure Mobilité.
