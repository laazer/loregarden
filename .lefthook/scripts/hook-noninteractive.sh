#!/usr/bin/env bash
# Source at the top of lefthook/bash hook scripts so agent and CI runs never block on prompts.
# shellcheck disable=SC2034
set -o pipefail 2>/dev/null || true

# Git exports GIT_DIR into hooks when pushing from a worktree (an absolute path to that
# worktree's gitdir; unset from a primary checkout). Suites that build throwaway repos in
# tmp_path and shell out to git with cwd= inherit it, and GIT_DIR beats cwd — so `git add .`
# runs against the loregarden gitdir and exits 128. Unset so git resolves through cwd, as CI
# does. No-op from a primary checkout, where git sets neither.
unset GIT_DIR GIT_WORK_TREE

export CI="${CI:-1}"
export GIT_TERMINAL_PROMPT=0
export GH_PROMPT_DISABLED=1
export NPM_CONFIG_YES="${NPM_CONFIG_YES:-true}"
export NPX_YES="${NPX_YES:-true}"
export DEBIAN_FRONTEND=noninteractive
