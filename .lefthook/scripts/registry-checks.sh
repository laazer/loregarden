#!/usr/bin/env bash
# Pre-commit: the registry checks — tests whose fixture is the source tree.
#
#   bash .lefthook/scripts/registry-checks.sh <staged files…>
#
# Some rules here have the shape "a new X must be registered in Y": a REST
# route must run an operation and cut its body from the args model, a bounded
# scroll region needs a census entry and its own keyboard test, a class name
# needs a rule, a polling surface needs `usePolledReadFailure`. Each is checked
# by a test that walks the tree, not by a test of X, so neither pre-push's
# import-graph selection nor `jest --changedSince` reaches it from the file you
# changed. They used to fire only on the full suite, about 70 minutes after
# the commit (queue + pytest + jest); each runs in seconds on its own.
#
# A test opts in where it is written, so this list cannot go stale:
#   - pytest: `pytestmark = pytest.mark.registry` (or the mark on one test);
#   - jest:   a `// @registry-check` line in the file.
# A whole-tree test that is fast belongs here. One that needs the database at
# volume, a network or a long timeout does not.
#
# Finding no opted-in tests at all is an error, not a pass: a broken search
# would otherwise examine nothing and report green.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

server=0
client=0
for f in "$@"; do
  case "$f" in
    # AGENTS.md: test_code_map holds its "where to look" table to the code.
    AGENTS.md | server/loregarden/* | server/tests/*) server=1 ;;
    client/src/*) client=1 ;;
  esac
done

# A test is a registry check by what its own file says. Plain loops, not
# mapfile: macOS's /bin/bash is 3.2.
status=0
# The two halves are independent; run them side by side, each into its own
# log, so a commit touching both costs the slower one rather than the sum.
logs="$(mktemp -d)"
trap 'rm -rf "$logs"' EXIT
pids=()
names=()

run_server() {
  local tests=()
  while IFS= read -r f; do tests+=("$f"); done < <(
    cd "$ROOT/server" && grep -rlE --include='test_*.py' 'pytest\.mark\.registry\b' tests | sort
  )
  if [ "${#tests[@]}" -eq 0 ]; then
    echo "registry-checks: no server test carries pytest.mark.registry; the search is broken" >&2
    return 1
  fi
  echo "registry-checks: pytest -m registry over ${#tests[@]} file(s)"
  cd "$ROOT/server"
  env -u GIT_DIR -u GIT_WORK_TREE bash "$SCRIPT_DIR/server_python.sh" -m pytest \
    -q -p no:cacheprovider -m registry --no-header "${tests[@]}"
}

run_client() {
  # shellcheck source=ensure-node.sh
  source "$SCRIPT_DIR/ensure-node.sh"
  if [ ! -x "$ROOT/client/node_modules/.bin/jest" ]; then
    echo "registry-checks: client/node_modules missing (cd client && npm ci)." >&2
    return 1
  fi
  local tests=()
  while IFS= read -r f; do tests+=("$f"); done < <(
    cd "$ROOT/client" && grep -rlE --include='*.test.ts' --include='*.test.tsx' '^// @registry-check' src | sort
  )
  if [ "${#tests[@]}" -eq 0 ]; then
    echo "registry-checks: no client test carries '// @registry-check'; the search is broken" >&2
    return 1
  fi
  echo "registry-checks: jest over ${#tests[@]} file(s)"
  cd "$ROOT/client"
  CI=1 ./node_modules/.bin/jest --silent --runTestsByPath "${tests[@]}"
}

if [ "$server" -eq 1 ]; then
  (run_server) >"$logs/server.log" 2>&1 &
  pids+=("$!"); names+=(server)
fi
if [ "$client" -eq 1 ]; then
  (run_client) >"$logs/client.log" 2>&1 &
  pids+=("$!"); names+=(client)
fi

i=0
while [ "$i" -lt "${#pids[@]}" ]; do
  name="${names[$i]}"
  if wait "${pids[$i]}"; then
    tail -n 3 "$logs/$name.log"
  else
    status=1
    echo "registry-checks: $name half FAILED:" >&2
    cat "$logs/$name.log" >&2
  fi
  i=$((i + 1))
done

exit "$status"
