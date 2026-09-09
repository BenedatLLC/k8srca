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
#   denies  the host itself (INPUT) and the host gateway, RFC1918 (10/8, 172.16/12, 192.168/16),
#           link-local incl. cloud metadata 169.254.169.254, and CGNAT
#   allows  traffic within the sandbox network itself (so k8stools is reachable)
#   allows  everything else, i.e. the public internet
#   EXEMPTS the k8stools container as a SOURCE -- it is the one member of this
#           network that legitimately needs to reach a private address, because
#           the cluster API server is one. Without this the rules break the
#           thing they are meant to protect: the sandbox is denied the API
#           server (correct) and so is k8stools (wrong).
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
CHAIN="DOCKER-USER"     # forwarded traffic: container -> elsewhere
HOST_CHAIN="INPUT"      # host-directed traffic: container -> the host itself
TAG="k8srca:${NET}"

subnet() {
  docker network inspect "$NET" -f '{{range .IPAM.Config}}{{.Subnet}}{{end}}' 2>/dev/null \
    || { echo "network $NET not found" >&2; exit 1; }
}

gateway() {
  docker network inspect "$NET" -f '{{range .IPAM.Config}}{{.Gateway}}{{end}}' 2>/dev/null
}

# Docker names a bridge br-<first 12 chars of the network id>.
bridge_iface() {
  local id; id="$(docker network inspect "$NET" -f '{{.Id}}' 2>/dev/null)"
  [[ -n "$id" ]] && echo "br-${id:0:12}"
}

# IP of the container that holds the cluster credential. It is the only member
# of this network allowed to leave it for a private address.
trusted_source() {
  docker inspect "${K8STOOLS_CONTAINER:-k8srca-k8stools}" \
    -f "{{(index .NetworkSettings.Networks \"$NET\").IPAddress}}" 2>/dev/null || true
}

apply() {
  local sn; sn="$(subnet)"
  [[ -n "$sn" ]] || { echo "could not determine subnet for $NET" >&2; exit 1; }
  local trusted; trusted="$(trusted_source)"
  echo "restricting egress from $NET ($sn)"
  remove_quiet

  local i=1
  # 1. The credential holder is exempt: it must reach the API server, which is
  #    at a private address in most deployments.
  if [[ -n "$trusted" ]]; then
    echo "  exempting k8stools at $trusted (needs the API server)"
    iptables -I "$CHAIN" $i -s "$trusted" -m comment --comment "$TAG" -j RETURN
    i=$((i+1))
  else
    echo "  WARNING: k8stools container not found on $NET." >&2
    echo "           If it runs there and the API server is on a private" >&2
    echo "           address, these rules will break it. Start it first, or" >&2
    echo "           set K8STOOLS_CONTAINER." >&2
  fi

  # 2. Intra-network traffic, so the sandbox can still call k8stools.
  iptables -I "$CHAIN" $i -s "$sn" -d "$sn"           -m comment --comment "$TAG" -j RETURN
  i=$((i+1))

  # 3. Everything else on this network is denied a private destination.
  for dst in 169.254.0.0/16 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 100.64.0.0/10; do
    iptables -I "$CHAIN" $i -s "$sn" -d "$dst"        -m comment --comment "$TAG" -j DROP
    i=$((i+1))
  done

  # 4. Traffic to the HOST ITSELF. DOCKER-USER sits in FORWARD and never sees
  #    this: a packet addressed to the bridge gateway is delivered locally and
  #    traverses INPUT instead. Without these rules the sandbox reaches
  #    anything the host has bound to the gateway address -- which, when the
  #    cluster API server arrives over an SSH forward bound there, is the
  #    single thing the sandbox most needs not to reach.
  local gw iface; gw="$(gateway)"; iface="$(bridge_iface)"
  if [[ -n "$gw" && -n "$iface" ]]; then
    local j=1
    if [[ -n "$trusted" ]]; then
      iptables -I "$HOST_CHAIN" $j -i "$iface" -s "$trusted" -d "$gw" \
        -m comment --comment "$TAG" -j ACCEPT
      j=$((j+1))
    fi
    iptables -I "$HOST_CHAIN" $j -i "$iface" -s "$sn" -d "$gw" \
      -m comment --comment "$TAG" -j DROP
    echo "  denying $sn -> host $gw on $iface (INPUT)"
  else
    echo "  WARNING: could not determine gateway/bridge for $NET;" >&2
    echo "           host-directed traffic is NOT restricted." >&2
  fi
  # Extra hosts to deny, e.g. a kube-apiserver on a public address:
  #   K8SRCA_DENY="203.0.113.10/32 198.51.100.0/24" sudo ./docker/egress-rules.sh apply
  for dst in ${K8SRCA_DENY:-}; do
    iptables -I "$CHAIN" $i -s "$sn" -d "$dst"        -m comment --comment "$TAG" -j DROP
    i=$((i+1))
  done
  echo "applied. verify with: $0 status $NET"
}

remove_from() {
  local chain="$1"
  while iptables -S "$chain" 2>/dev/null | grep -q -- "--comment \"\?$TAG"; do
    local rule; rule="$(iptables -S "$chain" | grep -n -- "$TAG" | head -1 | cut -d: -f1)"
    iptables -D "$chain" $((rule - 1)) 2>/dev/null || break
  done
}

remove_quiet() {
  remove_from "$CHAIN"
  remove_from "$HOST_CHAIN"
}

case "${1:-}" in
  apply)  apply ;;
  remove) remove_quiet; echo "removed rules tagged $TAG" ;;
  status)
    found=0
    for ch in "$CHAIN" "$HOST_CHAIN"; do
      if iptables -S "$ch" 2>/dev/null | grep -q -- "$TAG"; then
        echo "[$ch]"; iptables -S "$ch" | grep -- "$TAG"; found=1
      fi
    done
    [[ $found -eq 1 ]] || echo "no rules tagged $TAG (egress is UNRESTRICTED)"
    ;;
  *) sed -n '2,40p' "$0"; exit 1 ;;
esac
