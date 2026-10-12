#!/usr/bin/env bash
# Generate a self-signed cert for LOCAL prod validation only.
# On the ECS box, replace this with Let's Encrypt via certbot
# (see deploy/prod/README.md, "TLS on ECS" section).

set -euo pipefail
CERT_DIR="$(cd "$(dirname "$0")" && pwd)/certs"
mkdir -p "$CERT_DIR"

if [[ -f "$CERT_DIR/fullchain.pem" && -f "$CERT_DIR/privkey.pem" ]]; then
    echo "Cert already exists at $CERT_DIR. Delete to regenerate."
    exit 0
fi

openssl req -x509 -nodes -days 365 \
    -newkey rsa:2048 \
    -keyout "$CERT_DIR/privkey.pem" \
    -out    "$CERT_DIR/fullchain.pem" \
    -subj "/CN=localhost" \
    -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"

chmod 600 "$CERT_DIR/privkey.pem"
echo "Self-signed cert written to $CERT_DIR (CN=localhost, 365 days)."
echo "Browsers will warn — that's expected for self-signed. Use -k with curl."
