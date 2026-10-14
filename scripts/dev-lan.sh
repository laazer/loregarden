#!/usr/bin/env bash
# The preflight for `task dev:lan`: refuse without an explicit opt-in, warn, and
# print the address another device should open. It starts nothing; the task
# then runs the same server + client pair as `task dev`, bound to 0.0.0.0.
#
# The opt-in is an environment variable, not just the task name, because the
# API has no authentication the browser client can supply: it never sends
# LOREGARDEN_API_TOKEN, so setting one locks the phone out too. Whoever can
# reach the port can write files and spawn agents. The task name is easy to
# recall from shell history on a network you do not trust; the variable makes
# that a decision made each time.
#
# The URL printed is the client's. The API rides the same origin through the
# Vite proxy (client/vite.config.ts), so never point VITE_API_BASE at this
# address: a renewed DHCP lease would leave the page talking to a dead host.
set -uo pipefail

PORT="${LOREGARDEN_CLIENT_PORT:-5173}"
API_PORT="${LOREGARDEN_DEV_PORT:-8000}"

# First non-loopback IPv4 for the interface carrying the default route, then
# the usual macOS Wi-Fi/Ethernet interfaces, then Linux. Prints nothing when
# there is none; the caller says so.
lan_ipv4() {
  local iface addr
  iface="$(route -n get default 2>/dev/null | awk '/interface:/ {print $2; exit}')"
  for candidate in "$iface" en0 en1; do
    [[ -n "$candidate" ]] || continue
    addr="$(ipconfig getifaddr "$candidate" 2>/dev/null)"
    if [[ "$addr" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ && "$addr" != 127.* ]]; then
      printf '%s\n' "$addr"
      return 0
    fi
  done
  addr="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (i = 1; i < NF; i++) if ($i == "src") {print $(i + 1); exit}}')"
  if [[ "$addr" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ && "$addr" != 127.* ]]; then
    printf '%s\n' "$addr"
    return 0
  fi
  return 1
}

bar='!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!'

if [[ "${LOREGARDEN_DEV_LAN_ACK:-}" != "1" ]]; then
  cat >&2 <<EOF
$bar
dev:lan refused: set LOREGARDEN_DEV_LAN_ACK=1 to serve on the local network.

It binds 0.0.0.0, so anyone who can reach this machine can use an API that
writes files and spawns agent processes, with no authentication: the browser
client sends no LOREGARDEN_API_TOKEN, so a token would lock your device out
too. Only do this on a network you trust:

    LOREGARDEN_DEV_LAN_ACK=1 task dev:lan
$bar
EOF
  exit 1
fi

cat >&2 <<EOF
$bar
  WARNING: serving loregarden to the LOCAL NETWORK (0.0.0.0)
  Anyone who can reach ports $PORT and $API_PORT can write files and spawn agent
  processes. There is no authentication. Stop it (Ctrl-C) when you are done.
$bar
EOF

if ip="$(lan_ipv4)"; then
  echo "Open on your other device:  http://$ip:$PORT/" >&2
else
  echo "No LAN IPv4 address found (checked the default route's interface, en0, en1, and \`ip route\`)." >&2
  echo "Are you on a network? Find this machine's address yourself and open http://<address>:$PORT/" >&2
fi
