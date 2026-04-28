#!/bin/bash
# Run the full test suite locally (outside Docker).
# Requires: active virtualenv with requirements installed,
#            a running PostgreSQL/PostGIS database,
#            and GDAL Python bindings matching the system GDAL version.
#
# To run in Docker instead: ./docker/docker-manage.sh test

set -euo pipefail

if [ "X$VIRTUAL_ENV" = "X/vagrant/venv.vagrant" ] || \
   [ "X${USER:-}" = "Xvagrant" ]; then
    echo "Using vagrant virtualenv"
    . venv.vagrant/bin/activate
elif [ -d venv ]; then
    . venv/bin/activate
fi

# Emacs flycheck leaves stale temp files that confuse black and flake8.
rm -fv */flycheck_views.py */flycheck_models.py 2>/dev/null || true

black --check --verbose \
    --line-length 79 \
    --extend-exclude '(migrations|venv|venv.vagrant)' \
    .

flake8 \
    --tee \
    --output-file flake8.report \
    --exclude=__pycache__,migrations,venv,venv.vagrant

npm run test-js

python3 manage.py makemigrations --check
python3 manage.py migrate
python3 -Wa manage.py test
