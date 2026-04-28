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
    && pip install --no-cache-dir "GDAL==$(gdal-config --version)"

COPY . .

EXPOSE 8000
