# Same OS as production (Ubuntu 26.04 LTS), so dev uses the
# distribution's Python 3.14 and GDAL rather than different builds.
FROM ubuntu:26.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH

# GeoDjango system dependencies + C++ compiler for GDAL Python bindings
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-dev \
    python3-venv \
    build-essential \
    binutils \
    libproj-dev \
    gdal-bin \
    libgdal-dev \
    gettext \
    && rm -rf /var/lib/apt/lists/*

# The system Python is externally managed (PEP 668); install into a
# venv, which also puts `python` and `pip` on PATH.
RUN python3 -m venv "$VIRTUAL_ENV"

WORKDIR /app

COPY requirements.txt .
# GDAL Python bindings must match the installed system GDAL version.
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir "GDAL==$(gdal-config --version)" \
    # flake8-django 1.4 pins astroid<3.0, but astroid 2.x cannot parse
    # the PEP 695 `type` statement used inside Python 3.12's own
    # typing.py -- it crashes flake8-django's DJ0 check on any file
    # whose import chain leads astroid to infer through typing.py
    # (e.g. modeltranslation.translator). astroid 3.x fixes the
    # parser; its API is unchanged for flake8-django's narrow usage.
    # Installed as a second, deliberately conflicting step (like
    # GDAL above) since requirements.txt can't declare it without
    # pip's resolver rejecting the two constraints outright.
    && pip install --no-cache-dir "astroid==3.3.11"

COPY . .

EXPOSE 8000
