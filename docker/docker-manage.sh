#!/bin/bash

set -euo pipefail

# Resolve paths relative to the project root, not the caller's cwd.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE="docker compose -f $SCRIPT_DIR/docker-compose.yml"

export SSH_USER=$LOGNAME

BUILD_NOCACHE=""
while getopts "b" opt; do
    case "$opt" in
	b) BUILD_NOCACHE=1 ;;
	*) ;;
    esac
done
shift $((OPTIND - 1))

action="${1:-}"
shift || true

maybe_build_nocache() {
    local service="$1"
    if [ -n "$BUILD_NOCACHE" ]; then
	echo "Explicit build requested."
	$COMPOSE build --no-cache "$service"
    fi
}

case "$action" in
    up)
	maybe_build_nocache web
	$COMPOSE up -d db web
	echo "Web server running at http://localhost:8000"
	;;

    down)
	$COMPOSE down
	;;

    test)
	# Run the full test suite: Python lint, Django tests, JS tests.
	maybe_build_nocache web
	$COMPOSE run --rm web bash -c "
	    set -e
	    black --check --line-length 79 \
	        --extend-exclude '(migrations|venv)' . &&
	    flake8 --exclude=__pycache__,migrations,venv &&
	    python manage.py makemigrations --check &&
	    python manage.py migrate &&
	    coverage run --source='.' manage.py test &&
	    coverage report
	"
	cd "$PROJECT_ROOT" && npm install && npm run test-js
	;;

    sh)
	# Open a bash shell in the dev container, starting it if necessary.
	# The container is stopped automatically when no interactive shells remain.
	maybe_build_nocache dev-sh
	$COMPOSE up -d dev-sh
	$COMPOSE exec dev-sh bash || true
	# After the shell exits, check whether other shells are still open.
	num_shells=$($COMPOSE exec dev-sh \
	    ps -eo tty,comm 2>/dev/null \
	    | awk '$1 ~ /^pts\// && $2=="bash" {print $1}' \
	    | sort -u | wc -l || echo 0)
	if [ "$num_shells" -eq 0 ]; then
	    echo "No more shells running, stopping dev-sh..."
	    $COMPOSE stop dev-sh
	else
	    echo "${num_shells} shell(s) still running in dev-sh."
	fi
	;;

    logs)
	$COMPOSE logs -f "$@"
	;;

    migrate)
	$COMPOSE run --rm web python manage.py migrate
	;;

    makemigrations)
	$COMPOSE run --rm web python manage.py makemigrations "$@"
	;;

    manage)
	# Pass arbitrary manage.py commands: ./docker-manage.sh manage shell
	$COMPOSE run --rm web python manage.py "$@"
	;;

    build)
	# Build the production image and optionally push it.
	# Set REGISTRY=registry.example.com/mobilito before calling to push.
	maybe_build_nocache web
	$COMPOSE build web
	if [ -n "${REGISTRY:-}" ]; then
	    $COMPOSE push web
	else
	    echo "Image built. Set REGISTRY= to push."
	fi
	;;

    *)
	echo "Usage: ./docker/docker-manage.sh [-b] <action> [args]"
	echo ""
	echo "Actions:"
	echo "  up              Start db and web services"
	echo "  down            Stop all services"
	echo "  test            Run full test suite (lint + Django + JS)"
	echo "  sh              Open a shell in the dev container"
	echo "  logs [service]  Follow logs (default: all services)"
	echo "  migrate         Run pending database migrations"
	echo "  makemigrations  Generate new migrations"
	echo "  manage <cmd>    Run an arbitrary manage.py command"
	echo "  build           Build (and optionally push) the production image"
	echo ""
	echo "Flags:"
	echo "  -b  Rebuild the container image from scratch (--no-cache)"
	if [ -n "${action}" ]; then
	    echo ""
	    echo "Unrecognised action: \"${action}\""
	    exit 1
	fi
	;;
esac
