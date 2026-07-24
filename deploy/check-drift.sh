#!/usr/bin/env bash
# Guard: does each RUNNING container still match the compose file it was created from?
#
# Docker Compose stamps every container it creates with a `com.docker.compose.config-hash`
# label — a digest of that service's fully-interpolated config. Recompute the hash from the
# file and compare: a mismatch means the running container was built from a config that no
# longer matches this file, and nothing else will tell you. That gap is not hypothetical here.
# Keycloak ran for THREE DAYS with `--optimized` in the file it was created from while a fixed
# file said otherwise; the drift was invisible until a restart turned it into a 502. Same shape
# as the two silent-drift bugs this project has already paid for: `superseded_by` (the ORM knew,
# the SQL filter did not) and `_corpus_contains` (retrieval knew, the verifier did not). Two
# sources of one truth, and no one watching the seam between them.
#
# Exit 1 on any drift, so this belongs in the deploy sequence, not in someone's memory — run it
# after every `up -d`. A checker that is only run by hand is run once. This is the container-
# level analogue of `app.cli.check_sectors`, which does the same for a document's sector.
#
# It reads only labels and file hashes: no container is touched, nothing is changed, and it
# never reaches ATOB (it resolves containers by this stack's own project name).
#
#   deploy/check-drift.sh                              # the live cohost stack
#   deploy/check-drift.sh /path/to/other-compose.yml   # any compose file + its containers
#
# The compose file must sit in its real project directory — the hash resolves relative build
# contexts (`build: ./backend`, `./frontend`) against it, so a copy run from /tmp reports the
# build-context services as drift that is only an artifact of the moved path. This uses the
# file's own dirname as the project directory for exactly that reason; do not point it at a
# copy parked elsewhere. (Learned the honest way: a first "positive control" from /tmp flagged
# api and keycloak for a change that never landed — the drift was the path, not the config.)

set -euo pipefail

COMPOSE_FILE="${1:-/opt/luxmedicine/docker-compose.cohost.yml}"
ENV_FILE="${2:-/opt/luxmedicine/deploy/.env.prod}"
PROJECT_DIR="$(dirname "$COMPOSE_FILE")"

compose() {
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" --project-directory "$PROJECT_DIR" "$@"
}

# Sanity check the project directory BEFORE trusting any hash, because a wrong one is the trap
# that cost the 40 minutes: a `build:` context that does not resolve here means compose hashes
# the build config differently, and every build service reports a drift that is pure artifact.
# Catch it directly rather than leave the next person to rediscover it from red output.
context_artifact=0
while read -r ctx; do
  [ -z "$ctx" ] && continue
  case "$ctx" in
    /*) resolved="$ctx" ;;
     *) resolved="$PROJECT_DIR/$ctx" ;;
  esac
  if [ ! -d "$resolved" ]; then
    echo "WARNING: build context '$ctx' does not exist under $PROJECT_DIR"
    context_artifact=1
  fi
done < <(compose config --format json 2>/dev/null \
  | python3 -c 'import sys,json;[print(s.get("build",{}).get("context","")) for s in json.load(sys.stdin).get("services",{}).values() if isinstance(s.get("build"),dict)]' 2>/dev/null)

if [ "$context_artifact" -ne 0 ]; then
  echo "  -> run this from the file's own project directory; build-service hashes are unreliable here."
  echo
fi

# On drift, the hash alone says a service changed but not WHICH field. explain-drift.py diffs
# the `command` and `environment` fields — where the two faults this guard exists for lived
# (Keycloak's --optimized; the dropped GEMINI_API_KEY) — between the file and the container.
# A separate file, not an inline heredoc: `python3 -` would take its PROGRAM from stdin, the
# same stdin the compose JSON needs, so the two collide. Piping JSON to a real script is the
# clean shape.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
explain_drift() {
  local service="$1" cid="$2"
  compose config --format json 2>/dev/null \
    | python3 "$SCRIPT_DIR/explain-drift.py" "$service" "$cid"
}

drift=0
checked=0

# `config --hash '*'` prints one "<service> <hash>" line per service, computed from the file
# with the SAME env interpolation the container was created under — which is why --env-file is
# threaded through. Comparing raw hashes would be wrong without it.
while read -r service file_hash; do
  [ -z "$service" ] && continue
  checked=$((checked + 1))

  cid="$(compose ps -q "$service" 2>/dev/null | head -1)"
  if [ -z "$cid" ]; then
    echo "DRIFT: $service — declared in the file but no container is running"
    drift=1
    continue
  fi

  run_hash="$(docker inspect "$cid" \
    --format '{{index .Config.Labels "com.docker.compose.config-hash"}}')"

  if [ "$run_hash" != "$file_hash" ]; then
    echo "DRIFT: $service — file=${file_hash:0:16} running=${run_hash:0:16} (recreate it)"
    explain_drift "$service" "$cid" || true
    drift=1
  else
    echo "ok: $service (${file_hash:0:16})"
  fi
done < <(compose config --hash '*')

echo
if [ "$drift" -ne 0 ]; then
  echo "DRIFT found — the running stack does not match $COMPOSE_FILE. Run: docker compose ... up -d"
  exit 1
fi
echo "no drift: $checked services, every running container matches the file"
