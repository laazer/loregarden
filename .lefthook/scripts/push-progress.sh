# Step progress for the pre-push suites. Source it, then:
#
#   progress_begin "server-tests" 5     # name, number of steps
#   progress_step "ruff check"          # closes the step before it, opens this one
#   ...
#   trap 'progress_finish $?' EXIT      # the summary: every step, its time, the one that failed
#
# Each step prints `pre-push server-tests [2/5] ruff format --check` as it
# starts, so a terminal, an agent reading buffered output and a person
# scrolling back all see the same landmark. Under `loregarden capacity run`
# ($LOREGARDEN_PROGRESS_FILE set) the step is also written to that file, and
# the holder copies it onto the lease, where the queue board and every waiter
# behind this push read it. The test runners' own reporters write the same file
# with a count (pytest_push_progress.py, jest_push_progress.cjs);
# PUSH_PROGRESS_STEP tells them which step they are.
#
# Writing the file is best-effort by design: progress is a view of the run, and
# a view that fails must not fail the push.

_pp_name=""
_pp_total=0
_pp_index=0
_pp_started=0
_pp_step_started=0
_pp_steps=()
_pp_times=()
#: The step the judge has already reported for, so progress_finish leaves it
#: alone. Writing both would replace the judge's count with a bare FAILED line
#: and two NULLs — the shape 13 live leases carry today.
_pp_reported_index=0

progress_begin() {
  _pp_name="$1"
  _pp_total="$2"
  _pp_started=$SECONDS
}

# Replace the progress file by rename, so its reader never sees half of one.
# Only labels this repo wrote reach here; quotes and backslashes are escaped
# anyway, since a malformed report is logged on every write.
progress_write() {
  local file="${LOREGARDEN_PROGRESS_FILE:-}"
  [ -n "$file" ] || return 0
  local step="${1//\\/\\\\}"
  step="${step//\"/\\\"}"
  local tmp="$file.$$.tmp"
  # silent-ok: progress is a view of the run; a write that fails leaves the last report showing
  { printf '{"step": "%s"}\n' "$step" >"$tmp" && mv -f "$tmp" "$file"; } 2>/dev/null || rm -f "$tmp" 2>/dev/null
  return 0
}

# Where test-count-guard.sh asks the judge to compose this step's report.
# Beside the progress file, so putting it in place is a rename and a reader
# never sees half a report. Non-zero when nobody asked for progress.
progress_report_path() {
  local file="${LOREGARDEN_PROGRESS_FILE:-}"
  [ -n "$file" ] || return 1
  printf '%s.judge.%s.tmp' "$file" "$$"
}

# The judge's report IS this step's final write: it carries the outcome in words
# and the count the judge judged, so the terminal and the queue board can never
# disagree about how many tests ran. Written once, after the runner has exited,
# when exactly one writer is live.
progress_report_commit() {
  local src="$1"
  local file="${LOREGARDEN_PROGRESS_FILE:-}"
  if [ -z "$file" ] || [ ! -s "$src" ]; then
    # silent-ok: no progress file (or no report) means nothing to put in place;
    # the judge's own line has already said the outcome on the terminal
    rm -f "$src" 2>/dev/null || true
    return 0
  fi
  # silent-ok: progress is a view of the run; a write that fails leaves the last
  # report showing, and the terminal still carries the judge's verdict
  mv -f "$src" "$file" 2>/dev/null || rm -f "$src" 2>/dev/null || true
  _pp_reported_index="$_pp_index"
  return 0
}

_pp_close_step() {
  [ "$_pp_index" -gt 0 ] || return 0
  _pp_times+=("$((SECONDS - _pp_step_started))")
}

progress_step() {
  _pp_close_step
  _pp_index=$((_pp_index + 1))
  _pp_step_started=$SECONDS
  _pp_steps+=("$1")
  PUSH_PROGRESS_STEP="$1 ($_pp_index/$_pp_total)"
  export PUSH_PROGRESS_STEP
  echo "pre-push $_pp_name [$_pp_index/$_pp_total] $1 ..."
  progress_write "$PUSH_PROGRESS_STEP"
}

_pp_seconds() {
  local s="$1"
  if [ "$s" -lt 60 ]; then
    printf '%ss' "$s"
  else
    printf '%sm%02ds' "$((s / 60))" "$((s % 60))"
  fi
}

progress_finish() {
  local rc="$1"
  [ "$_pp_index" -gt 0 ] || return 0
  _pp_close_step
  local i last=$((_pp_index - 1)) mark
  echo ""
  if [ "$rc" -eq 0 ]; then
    echo "pre-push $_pp_name: passed, $_pp_index/$_pp_total steps in $(_pp_seconds $((SECONDS - _pp_started)))"
  else
    echo "pre-push $_pp_name: FAILED (exit $rc) at step $_pp_index/$_pp_total, ${_pp_steps[$last]}, after $(_pp_seconds $((SECONDS - _pp_started)))"
  fi
  for i in "${!_pp_steps[@]}"; do
    mark="ok    "
    [ "$rc" -ne 0 ] && [ "$i" -eq "$last" ] && mark="FAILED"
    printf '  %s %-32s %s\n' "$mark" "${_pp_steps[$i]}" "$(_pp_seconds "${_pp_times[$i]}")"
  done
  if [ "$rc" -ne 0 ]; then
    echo "  (the first failure is printed above the summary; read it before any that follow)"
    if [ "$_pp_reported_index" -ne "$_pp_index" ]; then
      progress_write "FAILED: ${_pp_steps[$last]} ($_pp_index/$_pp_total)"
    fi
  fi
  return 0
}
