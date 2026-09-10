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

echo
echo "The egress rules need root. To install that unit too:"
echo "  sudo sed \"s|@WORKDIR@|$HERE|g\" $HERE/systemd/k8srca-egress.service \\"
echo "    > /etc/systemd/system/k8srca-egress.service"
echo "  sudo systemctl daemon-reload && sudo systemctl enable --now k8srca-egress"
echo
echo "And so the user unit starts without you logging in:"
echo "  sudo loginctl enable-linger \"$USER\""
