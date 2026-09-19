#!/usr/bin/env bash
# Generate self-signed development TLS certificates for Wildframe.
# Output: apps/web/certificates/localhost.pem and localhost-key.pem
# SANs: DNS:localhost, IP:127.0.0.1, IP:::1, IP:192.168.1.14
# Permissions: 644 (Caddy/Grafana containers read as non-root)
# Idempotent: skips if both files already exist.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CERT_DIR="$REPO_ROOT/apps/web/certificates"
KEY_FILE="$CERT_DIR/localhost-key.pem"
CRT_FILE="$CERT_DIR/localhost.pem"

mkdir -p "$CERT_DIR"

if [[ -f "$KEY_FILE" && -f "$CRT_FILE" ]]; then
  echo "Dev certificates already exist at $CERT_DIR — skipping generation."
  echo "  $KEY_FILE"
  echo "  $CRT_FILE"
  echo "Delete them to regenerate."
  exit 0
fi

echo "Generating self-signed dev certificates..."

# Try modern openssl with -addext first; fall back to config file for older versions.
if openssl req -x509 -newkey rsa:2048 \
  -keyout "$KEY_FILE" -out "$CRT_FILE" \
  -days 365 -nodes \
  -subj "/CN=localhost" \
  -addext "subjectAltName=DNS:localhost,IP:127.0.0.1,IP:::1,IP:192.168.1.14" 2>/dev/null; then
  :
else
  echo "openssl -addext not supported, using config fallback..."
  TMP_CNF="$(mktemp)"
  cat > "$TMP_CNF" <<'CNF'
[req]
distinguished_name=req_distinguished_name
x509_extensions=v3_req
prompt=no
[req_distinguished_name]
CN=localhost
[v3_req]
subjectAltName=DNS:localhost,IP:127.0.0.1,IP:::1,IP:192.168.1.14
CNF
  openssl req -x509 -newkey rsa:2048 \
    -keyout "$KEY_FILE" -out "$CRT_FILE" \
    -days 365 -nodes \
    -subj "/CN=localhost" \
    -config "$TMP_CNF" -extensions v3_req
  rm -f "$TMP_CNF"
fi

chmod 644 "$KEY_FILE" "$CRT_FILE"

echo "Generated:"
echo "  $KEY_FILE"
echo "  $CRT_FILE"
openssl x509 -noout -ext subjectAltName -in "$CRT_FILE" 2>/dev/null || true
echo "Permissions: $(stat -c %a "$KEY_FILE") $KEY_FILE, $(stat -c %a "$CRT_FILE") $CRT_FILE"
