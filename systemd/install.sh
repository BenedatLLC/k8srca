#!/bin/bash
# Install the reboot-persistence units. Run from the repo root.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"

echo "installing user unit (no root)..."
mkdir -p ~/.config/systemd/user
sed "s|@WORKDIR@|$HERE|g" "$HERE/systemd/k8srca-up.service" \
  > ~/.config/systemd/user/k8srca-up.service
systemctl --user daemon-reload
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
