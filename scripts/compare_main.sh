#!/usr/bin/env bash
# Runs tests/ on the working tree and on a base commit (default HEAD: the code
# without the uncommitted changes), each in a throwaway container against the
# local trainlog_db (database trainlog_test, rebuilt per run), then diffs the
# two route snapshots. Nothing is published on a port and no container outlives it.
#
#   scripts/compare_main.sh [base-commit]
set -euo pipefail
cd "$(dirname "$0")/.."
BASE=${1:-HEAD}
OUT=$(mktemp -d)

step() { echo; echo "=== [$(date +%T)] $*"; }

step "Finding trainlog_db's network"
NETWORK=$(docker inspect trainlog_db -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}' | awk '{print $1}')
echo "$NETWORK"

step "Checking out $BASE into $OUT/base"
git worktree add --quiet --detach "$OUT/base" "$BASE"
trap 'git worktree remove --force "$OUT/base"' EXIT
cp -r tests "$OUT/base/"  # the base commit may predate the suite

# requirements.txt moves; the image must match the code under test
step "Building the image (fast when nothing changed)"
docker compose build --progress=plain trainlog 2>&1 | grep --line-buffered -E '^#[0-9]+ (\[|DONE|CACHED|ERROR)' || true

run() {  # run <label> <checkout>
  step "Tests on $1 (full log: $OUT/$1.log)"
  echo "Rebuilding trainlog_test from the migrations first, then one line per test"
  # .git mounted at its own path: a worktree's .git file points there
  if docker run --rm --network "$NETWORK" --env-file .env \
    --user "$(id -u):$(id -g)" -e HOME=/tmp -e PYTHONDONTWRITEBYTECODE=1 \
    -v "$2":/code -v "$PWD/.git":"$PWD/.git":ro -v "$OUT":/out -w /code \
    -e TRAINLOG_SNAPSHOT="/out/$1.json" \
    --entrypoint python trainlog-trainlog -m unittest discover -v -s tests -t . \
    2>&1 | tee "$OUT/$1.log" \
    | grep --line-buffered -E 'Applying migration|setup complete|\.\.\. |^(FAIL|ERROR):|^Ran |^(OK|FAILED)'
  then :; fi
  # The test run's own verdict, as the pipeline's status is grep's
  grep -qE '^OK' "$OUT/$1.log" && echo ">>> $1: passed" || echo ">>> $1: FAILED"
}

run base "$OUT/base"
run current "$PWD"

step "Route status differences ($BASE -> working tree)"
python3 - "$OUT/base.json" "$OUT/current.json" <<'PY'
import json, sys
try:
    base, cur = (json.load(open(p)) for p in sys.argv[1:])
except FileNotFoundError as e:
    sys.exit(f"no snapshot: {e.filename} (see the log)")
diff = [f"{k}: {base.get(k)} -> {cur.get(k)}" for k in sorted(base.keys() | cur.keys()) if base.get(k) != cur.get(k)]
print("\n".join(diff) or "none")
PY
echo
echo "Full logs: $OUT/base.log $OUT/current.log"
