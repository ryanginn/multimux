#!/usr/bin/env bash
# Remove the MultiMux service. Keeps /etc/multimux (your config) unless --purge.
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "Run as root" >&2; exit 1; }
systemctl disable --now multimux.service 2>/dev/null || true
rm -f /etc/systemd/system/multimux.service /usr/local/bin/multimux
systemctl daemon-reload
rm -rf /opt/multimux /var/lib/multimux
if [ "${1:-}" = "--purge" ]; then
  rm -rf /etc/multimux
  userdel multimux 2>/dev/null || true
fi
echo "MultiMux removed. ODR tools and GNU Radio were left installed."
