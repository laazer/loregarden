#!/usr/bin/env bash
# Source at the top of lefthook/bash hook scripts so agent and CI runs never block on prompts.
# shellcheck disable=SC2034
set -o pipefail 2>/dev/null || true

export CI="${CI:-1}"
export GIT_TERMINAL_PROMPT=0
export GH_PROMPT_DISABLED=1
export NPM_CONFIG_YES="${NPM_CONFIG_YES:-true}"
export NPX_YES="${NPX_YES:-true}"
export DEBIAN_FRONTEND=noninteractive
