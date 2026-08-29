FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# GeoDjango system dependencies + C++ compiler for GDAL Python bindings
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    binutils \
    libproj-dev \
    gdal-bin \
    libgdal-dev \
    gettext \
    && rm -rf /var/lib/apt/lists/*

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
