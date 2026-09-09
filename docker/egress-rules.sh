#!/bin/bash
# Egress restriction for the sandbox network (design 001 §8.3).
#
# A user-defined bridge isolates the sandbox from the host's network namespace
# and from other containers, but it still NATs outbound traffic -- so by
# default a sandbox can reach the host, the LAN, cloud metadata, and (on most
# setups) the kube-apiserver. This installs rules in Docker's DOCKER-USER chain
# to stop that.
#
#   sudo ./docker/egress-rules.sh apply     [network]
#   sudo ./docker/egress-rules.sh status    [network]
#   sudo ./docker/egress-rules.sh remove    [network]
#
# WHAT THIS DOES:
#   denies  the host gateway, RFC1918 (10/8, 172.16/12, 192.168/16),
#           link-local incl. cloud metadata 169.254.169.254, and CGNAT
#   allows  traffic within the sandbox network itself (so k8stools is reachable)
#   allows  everything else, i.e. the public internet
#
# WHAT THIS DOES NOT DO:
#   It is not an allowlist. The sandbox can still reach arbitrary *public*
#   hosts, so this does not prevent exfiltration to the internet. Doing that
#   needs an egress proxy with a domain allowlist, because api.anthropic.com's
#   addresses are not stable enough to pin.
#
#   The threat it does address is the one that matters most here: the sandbox
#   runs model-authored bash, and must not be able to reach your cluster's API
#   server, your host, your LAN, or an instance metadata service.
#
# If the kube-apiserver is on a PUBLIC address, these rules do not block it.
# Add an explicit DROP for it -- see `apply` below.
set -euo pipefail

NET="${2:-k8srca-net}"
CHAIN="DOCKER-USER"
TAG="k8srca:${NET}"

subnet() {
  docker network inspect "$NET" -f '{{range .IPAM.Config}}{{.Subnet}}{{end}}' 2>/dev/null \
    || { echo "network $NET not found" >&2; exit 1; }
}

apply() {
  local sn; sn="$(subnet)"
  [[ -n "$sn" ]] || { echo "could not determine subnet for $NET" >&2; exit 1; }
  echo "restricting egress from $NET ($sn)"
  remove_quiet

  # Ordered: intra-network first (k8stools must stay reachable), then denies.
  iptables -I "$CHAIN" 1 -s "$sn" -d "$sn"            -m comment --comment "$TAG" -j RETURN
  local i=2
  for dst in 169.254.0.0/16 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 100.64.0.0/10; do
    iptables -I "$CHAIN" $i -s "$sn" -d "$dst"        -m comment --comment "$TAG" -j DROP
    i=$((i+1))
  done
  # Extra hosts to deny, e.g. a kube-apiserver on a public address:
  #   K8SRCA_DENY="203.0.113.10/32 198.51.100.0/24" sudo ./docker/egress-rules.sh apply
  for dst in ${K8SRCA_DENY:-}; do
    iptables -I "$CHAIN" $i -s "$sn" -d "$dst"        -m comment --comment "$TAG" -j DROP
    i=$((i+1))
  done
  echo "applied. verify with: $0 status $NET"
}

remove_quiet() {
  while iptables -S "$CHAIN" 2>/dev/null | grep -q -- "--comment \"\?$TAG"; do
    local rule; rule="$(iptables -S "$CHAIN" | grep -n -- "$TAG" | head -1 | cut -d: -f1)"
    iptables -D "$CHAIN" $((rule - 1)) 2>/dev/null || break
  done
}

case "${1:-}" in
  apply)  apply ;;
  remove) remove_quiet; echo "removed rules tagged $TAG" ;;
  status) iptables -S "$CHAIN" | grep -- "$TAG" || echo "no rules tagged $TAG (egress is UNRESTRICTED)" ;;
  *) sed -n '2,40p' "$0"; exit 1 ;;
esac
