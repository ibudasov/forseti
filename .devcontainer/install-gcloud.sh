#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
    echo "Run this installer as root." >&2
    exit 1
fi

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    gnupg

key_file="$(mktemp)"
keyring_file="/etc/apt/keyrings/cloud.google.gpg"
install -d -m 0755 /etc/apt/keyrings
curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg -o "${key_file}"
gpg --dearmor --yes --output "${keyring_file}" "${key_file}"
rm -f "${key_file}"
chmod 0644 "${keyring_file}"

echo "deb [signed-by=${keyring_file}] https://packages.cloud.google.com/apt cloud-sdk main" \
    > /etc/apt/sources.list.d/google-cloud-sdk.list
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends google-cloud-cli
rm -rf /var/lib/apt/lists/*
