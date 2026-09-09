#!/bin/bash
# Verify the sandbox network's egress restrictions (design 001 §8.3).
# Safe to run without root; it only probes from a throwaway container.
#
# CAVEAT: a probe cannot distinguish "blocked by firewall" from "nothing is
# listening there". On a host with no cloud metadata service and no 10/8
# route, those two rows read "blocked" whether or not any rule exists. The
# rows that actually carry signal are:
#   - host gateway: definitely exists and answers, so it flips from REACHABLE
#     to blocked exactly when the rules take effect. This is the real test.
#   - k8stools / api.anthropic.com: must stay REACHABLE, i.e. the rules did
#     not over-block.
# Treat the private-range rows as a smoke test, not proof.
set -uo pipefail
NET="${1:-k8srca-net}"
GW="$(docker network inspect "$NET" -f '{{range .IPAM.Config}}{{.Gateway}}{{end}}')"

docker run --rm -i --network "$NET" alpine:latest sh -s "$GW" <<'PROBE'
GW="$1"
probe() { # name host expected
  if timeout 4 nc -z -w2 "$2" "${4:-443}" 2>/dev/null || ping -c1 -W2 "$2" >/dev/null 2>&1; then r=REACHABLE; else r=blocked; fi
  if [ "$r" = "$3" ]; then echo "  ok   $1: $r"; else echo "  FAIL $1: $r (expected $3)"; fi
}
echo "egress probes:"
probe "host gateway      " "$GW"            blocked
probe "cloud metadata    " 169.254.169.254  blocked
probe "private 10.0.0.0/8" 10.255.255.1     blocked
probe "k8stools (in-net) " k8stools         REACHABLE 8000
probe "api.anthropic.com " api.anthropic.com REACHABLE
PROBE
