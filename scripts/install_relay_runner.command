#!/bin/bash
set -euo pipefail

label="com.cogito.relay-runner"
domain="gui/$(id -u)"
plist="/Users/cogito/Library/LaunchAgents/${label}.plist"

mkdir -p /Users/cogito/effiency/.relay/runner

if launchctl print "${domain}/${label}" >/dev/null 2>&1; then
  launchctl bootout "${domain}/${label}"
fi

launchctl bootstrap "${domain}" "${plist}"
launchctl kickstart -k "${domain}/${label}"
launchctl print "${domain}/${label}" | sed -n '1,80p'

echo
echo "Relay Runner installed and started."
echo "Logs: /Users/cogito/effiency/.relay/runner/"
read -r -p "Press Enter to close..."
