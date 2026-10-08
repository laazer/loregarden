#!/usr/bin/env bash
# Claude usage needs the interactive Claude Code login; the setup token cannot
# read it. Check it where a person is at the keyboard to fix it: the server
# cannot open a browser, and only says "run claude /login" after the fact.
# CLAUDE_CODE_OAUTH_TOKEN is unset for the check because `claude auth status`
# reports that token as logged in.
# LOREGARDEN_SKIP_CLAUDE_LOGIN_CHECK=1 skips it.
#
# Run before anything else shares the terminal: `task dev` calls it ahead of
# the server and client, because Vite reads stdin too and would take the answer.
set -uo pipefail
[[ "${LOREGARDEN_SKIP_CLAUDE_LOGIN_CHECK:-}" == "1" ]] && exit 0
claude_bin="${LOREGARDEN_CLAUDE_BIN:-claude}"
if ! command -v "$claude_bin" >/dev/null 2>&1; then
  echo "warning: '$claude_bin' not found — Claude agents and usage meters will not work." >&2
  exit 0
fi
status="$(env -u CLAUDE_CODE_OAUTH_TOKEN -u ANTHROPIC_API_KEY "$claude_bin" auth status --json 2>&1)" || true
if [[ "$status" =~ \"loggedIn\":[[:space:]]*true ]]; then
  exit 0
fi
if [[ ! "$status" =~ \"loggedIn\":[[:space:]]*false ]]; then
  echo "warning: could not read Claude login status: ${status:-no output}" >&2
  exit 0
fi
echo "Claude Code is logged out — the Usage modal cannot show live Claude limits." >&2
if [[ ! -t 0 ]]; then
  echo "  Run \`claude auth login\` in a terminal." >&2
  exit 0
fi
choice=""
# Times out to skip, so an unattended start never hangs on the prompt.
read -r -t 30 -p "  [l]og in now or [s]kip? (skips in 30s) [s] " choice || echo
case "$choice" in
  l|L|login)
    env -u CLAUDE_CODE_OAUTH_TOKEN -u ANTHROPIC_API_KEY "$claude_bin" auth login \
      || echo "warning: login did not complete — continuing without it." >&2
    ;;
  *) echo "  Skipped. Run \`claude auth login\` any time." >&2 ;;
esac
