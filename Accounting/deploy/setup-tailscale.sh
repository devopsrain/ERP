#!/bin/bash
# Steps 24-26 — install Tailscale for VPN remote access.
# Run ONCE with:  sudo bash deploy/setup-tailscale.sh
# It prints a login URL — open it in any browser and sign in to create/join your tailnet.
set -euo pipefail

curl -fsSL https://tailscale.com/install.sh | sh
tailscale up

echo ""
echo "Server tailnet IPv4 (use this for the Route 53 record in step 26):"
tailscale ip -4
