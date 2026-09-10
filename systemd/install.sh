#!/bin/bash
# Install the reboot-persistence units. Run from the repo root.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"

echo "installing user units (no root)..."
mkdir -p ~/.config/systemd/user
for unit in k8srca-up k8srca-tunnel; do
  sed "s|@WORKDIR@|$HERE|g" "$HERE/systemd/$unit.service" \
    > ~/.config/systemd/user/$unit.service
done
systemctl --user daemon-reload

# The tunnel is only needed when the API server is not routable from a
# container; enabling it otherwise would fail on every restart.
if grep -qE '^K8SRCA_SSH_HOST=.+' "$HERE/.env" 2>/dev/null; then
  systemctl --user enable --now k8srca-tunnel
  echo "  k8srca-tunnel enabled (Restart=always)"
else
  echo "  k8srca-tunnel NOT enabled: K8SRCA_SSH_HOST is unset in .env."
  echo "  That is correct if your API server is reachable from a container."
fi
systemctl --user enable --now k8srca-up
systemctl --user --no-pager status k8srca-up | head -5

# Without linger, user units wait for a first interactive login rather than
# starting at boot -- so the supervised tunnel would be missing after a reboot,
# and the symptom is the agent reporting it cannot reach the cluster while
# kubectl works fine. Offer to fix it here rather than print an instruction.
echo
if [ "$(loginctl show-user "$USER" --property=Linger 2>/dev/null)" = "Linger=yes" ]; then
  echo "linger is already enabled: user units will start at boot."
else
  echo "User units do not start at boot unless your user lingers."
  echo "Without it, the tunnel comes back only after you log in."
  if [ -t 0 ]; then
    printf 'Run `sudo loginctl enable-linger %s` now? [y/N] ' "$USER"
    read -r reply
    case "$reply" in
      [yY]*)
        if sudo loginctl enable-linger "$USER"; then
          echo "  enabled."
        else
          echo "  failed -- run it yourself: sudo loginctl enable-linger $USER" >&2
        fi
        ;;
      *) echo "  skipped. Run later: sudo loginctl enable-linger $USER" ;;
    esac
  else
    echo "  Run: sudo loginctl enable-linger $USER"
  fi
fi

echo
echo "The egress rules need root. To install that unit too:"
echo "  sudo sed \"s|@WORKDIR@|$HERE|g\" $HERE/systemd/k8srca-egress.service \\"
echo "    > /etc/systemd/system/k8srca-egress.service"
echo "  sudo systemctl daemon-reload && sudo systemctl enable --now k8srca-egress"
echo
echo "Check with: k8srca status"
