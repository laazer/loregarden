#!/usr/bin/env bash
# Sourced by hook-noninteractive.sh: put a native git first on PATH on Apple silicon.
#
# On an Apple-silicon Mac whose PATH leads to an Intel git, every git a hook or
# test spawns runs under Rosetta: 98ms per `git rev-parse` against 50ms native,
# measured 2026-11-25 under load. The rule is server/loregarden/services/
# native_git.py's, step for step, and server/tests/test_native_git.py holds the
# two to each other:
#
#   1. LOREGARDEN_GIT_BIN set: use it, unchecked.
#   2. Not Apple silicon (Linux CI, an Intel Mac): change nothing.
#   3. PATH's git is native and >= 2.38: change nothing. /usr/bin/git, the
#      xcrun shim, does not count.
#   4. The first native candidate >= 2.38 (merge-tree --write-tree).
#   5. Otherwise warn once, naming what to install, and change nothing.
#
# Applying a choice: a per-user directory holding a `git` symlink to it goes
# first on PATH, ahead of the libexec/git-core git puts there for hooks.
# GIT_EXEC_PATH is exported too, because Apple's git run through a symlink
# looks for its helpers under Xcode.app, which a CLT-only machine lacks.
# LOREGARDEN_GIT_BIN is exported so the server Python a hook starts uses the
# same git without asking again.
#
# Only children that are themselves native gain: a process running under
# Rosetta (an Intel bash, as /usr/local/bin/bash is) starts the x86_64 slice of
# a universal binary. pytest and the Python gates run on an arm64 Python, so
# the git they spawn is native; a hook script's own git is native only when
# its bash is.
#
# Bash 3.2-compatible: it may be sourced by /bin/bash.

LOREGARDEN_MIN_GIT_MAJOR=2
LOREGARDEN_MIN_GIT_MINOR=38
LOREGARDEN_NATIVE_GIT_CANDIDATES="${LOREGARDEN_NATIVE_GIT_CANDIDATES:-/opt/homebrew/bin/git:/Library/Developer/CommandLineTools/usr/bin/git:/Applications/Xcode.app/Contents/Developer/usr/bin/git}"

_native_git_has_arm64() {
  case "$(file -bL "$1" 2>/dev/null)" in
    *arm64*) return 0 ;;
  esac
  return 1
}

_native_git_version_ok() {
  local out major rest minor
  out="$("$1" --version 2>/dev/null)" || return 1
  out="${out#git version }"
  major="${out%%.*}"
  rest="${out#*.}"
  minor="${rest%%.*}"
  case "$major$minor" in
    '' | *[!0-9]*) return 1 ;;
  esac
  [ "$major" -gt "$LOREGARDEN_MIN_GIT_MAJOR" ] && return 0
  [ "$major" -eq "$LOREGARDEN_MIN_GIT_MAJOR" ] && [ "$minor" -ge "$LOREGARDEN_MIN_GIT_MINOR" ]
}

_native_git_qualifies() {
  [ -f "$1" ] && [ -x "$1" ] && _native_git_has_arm64 "$1" && _native_git_version_ok "$1"
}

# The three probes below are functions so test_native_git.py can redefine them
# after sourcing this, and run the rule on Linux CI.
_native_git_darwin() {
  case "${OSTYPE:-}" in darwin*) return 0 ;; esac
  return 1
}

# The hardware, not this process: an Intel bash says x86_64 to uname -m.
_native_git_apple_silicon() {
  _native_git_darwin && [ "$(sysctl -n hw.optional.arm64 2>/dev/null)" = "1" ]
}

# PATH without any loregarden-native-git-* shim directory, into _native_git_path.
# A shim is a symlink to Apple's git, which derives its helpers' directory from
# the path it was run by; it works only beside the GIT_EXEC_PATH that came with
# it. A child that dropped that export, or a hook with another TMPDIR, must not
# take an inherited shim for "PATH's git is native" (step 3). No fork: a hit
# here runs in every hook script.
_native_git_strip_shims() {
  local entry old_ifs
  _native_git_path=""
  old_ifs="$IFS"
  IFS=:
  for entry in $1; do
    case "$entry" in
      */loregarden-native-git-*) ;;
      *) _native_git_path="${_native_git_path:+$_native_git_path:}$entry" ;;
    esac
  done
  IFS="$old_ifs"
}

# Prints the git to put first on PATH; prints nothing to change nothing.
# A fallback to a translated git prints its warning to stderr, once per tree
# of processes.
loregarden_resolve_native_git() {
  [ -n "${LOREGARDEN_GIT_BIN:-}" ] && { printf '%s\n' "$LOREGARDEN_GIT_BIN"; return 0; }
  _native_git_apple_silicon || return 0
  local path_git candidate old_ifs
  _native_git_strip_shims "$PATH"
  path_git="$(PATH="$_native_git_path" command -v git 2>/dev/null || true)"
  if [ -n "$path_git" ] && [ "$path_git" != "/usr/bin/git" ] && _native_git_qualifies "$path_git"; then
    return 0
  fi
  old_ifs="$IFS"
  IFS=:
  for candidate in $LOREGARDEN_NATIVE_GIT_CANDIDATES; do
    if _native_git_qualifies "$candidate"; then
      IFS="$old_ifs"
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  IFS="$old_ifs"
  if [ -z "${LOREGARDEN_NATIVE_GIT_WARNED:-}" ]; then
    echo "loregarden: no native git >= $LOREGARDEN_MIN_GIT_MAJOR.$LOREGARDEN_MIN_GIT_MINOR found; PATH's git (${path_git:-none}) runs under Rosetta at about twice the cost. Install Apple's Command Line Tools (xcode-select --install), or point LOREGARDEN_GIT_BIN at a native git." >&2
    export LOREGARDEN_NATIVE_GIT_WARNED=1
  fi
  return 0
}

# Resolving forks a git --version per candidate, at Rosetta prices from an
# Intel bash (each fork cost ~90ms at load 80), and every hook script sources
# this. So the answer is kept in the shim directory, keyed by the inode and
# mtime of PATH's git and of every candidate: an upgrade, an install or a
# removal changes the key and resolves again. A hit costs one fork (stat).
loregarden_use_native_git() {
  local tmp shim cache key line choice exec_path warn path_wo
  _native_git_darwin || return 0
  tmp="${TMPDIR:-/tmp}"
  shim="${tmp%/}/loregarden-native-git-${UID:-$(id -u)}"
  # A parent hook already applied it.
  if [ -n "${LOREGARDEN_GIT_BIN:-}" ] && [ "${PATH%%:*}" = "$shim" ]; then
    return 0
  fi
  # Look past earlier shims, this one or a parent's, so PATH's own git is what
  # the key and rule see.
  _native_git_strip_shims "$PATH"
  path_wo="$_native_git_path"
  cache="$shim/choice"
  # A missing candidate makes stat exit 1; its absence is part of the key.
  # Every step here must survive the caller's `set -e`.
  # shellcheck disable=SC2086 # the candidate list splits on purpose
  key="$(stat -n -L -f '%N %i %m ' $(PATH="$path_wo" command -v git 2>/dev/null) ${LOREGARDEN_NATIVE_GIT_CANDIDATES//:/ } 2>/dev/null || true)|${LOREGARDEN_GIT_BIN:-}"
  choice="" exec_path="" warn="" line=""
  if [ -f "$cache" ] \
    && { IFS= read -r line && [ "$line" = "$key" ] && IFS= read -r choice && IFS= read -r exec_path && IFS= read -r warn; } < "$cache"; then
    :
  else
    mkdir -p "$shim" 2>/dev/null || { echo "loregarden: cannot create $shim; using PATH's git" >&2; return 0; }
    # Only step 5 writes to stderr; kept, so a cached fallback still warns.
    choice="$(PATH="$path_wo" LOREGARDEN_NATIVE_GIT_WARNED="" loregarden_resolve_native_git 2>"$cache.err.$$")"
    warn="$(cat "$cache.err.$$" 2>/dev/null || true)"
    rm -f "$cache.err.$$"
    case "$choice" in
      /*) exec_path="$("$choice" --exec-path 2>/dev/null)" || choice="" ;;
      *) choice="" ;;
    esac
    printf '%s\n%s\n%s\n%s\n' "$key" "$choice" "$exec_path" "$warn" > "$cache.$$" && mv -f "$cache.$$" "$cache" \
      || echo "loregarden: cannot write $cache; the next hook resolves git again" >&2
  fi
  if [ -n "$warn" ] && [ -z "${LOREGARDEN_NATIVE_GIT_WARNED:-}" ]; then
    echo "$warn" >&2
    export LOREGARDEN_NATIVE_GIT_WARNED=1
  fi
  [ -n "$choice" ] || return 0
  if [ ! -L "$shim/git" ] || [ ! -f "$cache" ] || [ "$line" != "$key" ]; then
    # Rename into place: hooks run in parallel and must never see a half-made link.
    ln -sfn "$choice" "$shim/git.$$" && mv -f "$shim/git.$$" "$shim/git" \
      || { echo "loregarden: cannot link $choice into $shim; using PATH's git" >&2; return 0; }
  fi
  PATH="$shim:$path_wo"
  export PATH GIT_EXEC_PATH="$exec_path" LOREGARDEN_GIT_BIN="$choice"
}
