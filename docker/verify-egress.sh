#!/bin/bash
# Verify the sandbox network's egress restrictions (design 001 §8.3).
# Safe without root: probes from a throwaway container on the sandbox network.
#
# The probe that carries the most signal is the API SERVER one. It is a real
# listening service, so "blocked" cannot be confused with "nothing there" --
# unlike a private-range or metadata probe on a host that has neither, which
# reads blocked whether or not any rule exists.
set -uo pipefail
NET="${1:-k8srca-net}"
GW="$(docker network inspect "$NET" -f '{{range .IPAM.Config}}{{.Gateway}}{{end}}')"
[[ -n "$GW" ]] || { echo "network $NET not found" >&2; exit 1; }

echo "sandbox-position probes on $NET (gateway $GW):"
docker run --rm -i --network "$NET" alpine:latest sh -s "$GW" 2>/dev/null <<'PROBE'
GW="$1"
fail=0
t() { # label host port expected
  if timeout 4 nc -z -w3 "$2" "$3" 2>/dev/null; then r=REACHABLE; else r=blocked; fi
  if [ "$r" = "$4" ]; then
    printf '  ok    %-38s %s\n' "$1" "$r"
  else
    printf '  FAIL  %-38s %s (expected %s)\n' "$1" "$r" "$4"; fail=1
  fi
}
echo "  (this container is $(hostname -i))"
# Must be denied. The API server is a live service, so this one is decisive:
# it traverses INPUT, not FORWARD, which DOCKER-USER alone does not cover.
t "host gateway:6443 (API server)"      "$GW" 6443 blocked
t "private 10.0.0.0/8"                  10.255.255.1 80 blocked
t "cloud metadata 169.254.169.254"      169.254.169.254 80 blocked
# Must keep working, or the rules have over-blocked.
t "k8stools:8000 (MCP)"                 k8stools 8000 REACHABLE
t "api.anthropic.com:443"               api.anthropic.com 443 REACHABLE
exit $fail
PROBE
rc=$?
echo
if [ $rc -eq 0 ]; then
  echo "PASS - sandbox is confined and can still reach what it needs"
else
  echo "FAIL - see above. If the API server is REACHABLE, the INPUT rules are"
  echo "       missing: DOCKER-USER filters forwarded traffic only, and traffic"
  echo "       to the host's own gateway address goes through INPUT."
  echo "       Re-run: sudo ./docker/egress-rules.sh apply $NET"
fi
exit $rc
