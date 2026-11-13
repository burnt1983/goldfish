#!/usr/bin/env bash
set -euo pipefail
rm -f \
  "${HOME}/.local/bin/puff" \
  "${HOME}/.local/bin/goldfish" \
  "${HOME}/.local/share/applications/goldfish.desktop" \
  "${HOME}/.config/autostart/goldfish.desktop" \
  "${HOME}/.cache/goldfish.pid" \
  "${HOME}/.local/share/icons/hicolor/256x256/apps/goldfish.png"
rm -rf "${HOME}/.local/share/goldfish"
echo "Removed the goldfish. Saved settings under ~/.config/goldfish were left in place."
