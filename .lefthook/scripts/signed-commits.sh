#!/usr/bin/env bash
# Pre-push: refuse to push a commit that carries no signature.
#
# main's ruleset requires signed commits and this repository signs every one
# (commit.gpgsign=true). Two agent runs on lg-durable-remote-336 committed with
# `git -c commit.gpgsign=false` and the unsigned commits reached PR #555. The
# PreToolUse hook .claude/hooks/git_signing_guard.py stops a Claude agent doing
# that; this stops every other way (another CLI, a script, a person in a hurry).
#
# Reads git's pre-push stdin: "<local ref> <local sha> <remote ref> <remote sha>".
# Checks only commits the remote does not already have. Checks for a signature
# header, not its validity: verifying needs gpg.ssh.allowedSignersFile, which
# most checkouts lack, and GitHub verifies on arrival anyway.
set -uo pipefail

if [ "$(git config --type=bool --get commit.gpgsign 2>/dev/null)" != "true" ]; then
  exit 0  # this repository does not sign; nothing to hold it to
fi

zero="0000000000000000000000000000000000000000"
unsigned=()
while read -r local_ref local_sha _remote_ref remote_sha; do
  [ -n "${local_sha:-}" ] || continue
  [ "$local_sha" = "$zero" ] && continue  # a branch deletion pushes no commits
  if [ "$remote_sha" != "$zero" ] && git cat-file -e "$remote_sha^{commit}" 2>/dev/null; then
    range=("$remote_sha..$local_sha")
  else
    range=("$local_sha" --not --remotes)
  fi
  if ! commits=$(git rev-list "${range[@]}"); then
    echo "signed-commits: could not list the commits being pushed for $local_ref" >&2
    exit 1
  fi
  for commit in $commits; do
    # The header block ends at the first blank line; the signature lives there.
    if ! git cat-file commit "$commit" | sed '/^$/q' | grep -q '^gpgsig'; then
      unsigned+=("$commit")
    fi
  done
done

if [ "${#unsigned[@]}" -eq 0 ]; then
  exit 0
fi

echo "pre-push: ${#unsigned[@]} commit(s) have no signature; main's ruleset requires one:" >&2
for commit in "${unsigned[@]}"; do
  echo "  $(git log -1 --format='%h %s' "$commit")" >&2
done
cat >&2 <<'HINT'

Re-sign them before pushing, from the oldest unsigned commit's parent:
  git rebase -r --exec 'git commit --amend --no-edit -S' <parent>
Never commit with -c commit.gpgsign=false or --no-gpg-sign. If signing itself
fails, fix the signer (key, ssh-agent) or ask the user.
HINT
exit 1
