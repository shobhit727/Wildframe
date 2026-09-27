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
KAFKA_KEYSTORE="$CERT_DIR/kafka-keystore.pem"
KAFKA_TRUSTSTORE="$CERT_DIR/kafka-truststore.pem"
KAFKA_KEYSTORE_PW="$CERT_DIR/kafka_keystore_password"
KAFKA_KEY_PW="$CERT_DIR/kafka_key_password"

mkdir -p "$CERT_DIR"

# cp-kafka reads the keystore and key passwords from FILES named by
# KAFKA_SSL_KEYSTORE_CREDENTIALS / KAFKA_SSL_KEY_CREDENTIALS, bind-mounted from
# here. Generated rather than hand-created so a clean checkout yields a
# working broker. Dev-only value: the matching private key is gitignored and
# the certificate is self-signed.
ensure_kafka_passwords() {
  local password="${KAFKA_SSL_KEYSTORE_PASSWORD:-wildframe-dev}"
  printf '%s' "$password" > "$KAFKA_KEYSTORE_PW"
  printf '%s' "$password" > "$KAFKA_KEY_PW"
  chmod 644 "$KAFKA_KEYSTORE_PW" "$KAFKA_KEY_PW"
}

# Kafka's PEM keystore support (SslEngineFactory$PemStore) has two requirements
# that the base pair above does not satisfy:
#
#   1. The private key and the certificate must be in ONE file, concatenated.
#   2. The key must be an *encrypted* PKCS#8 blob. cp-kafka's configure step
#      always sets ssl.key.password from $KAFKA_SSL_KEY_CREDENTIALS, and
#      Kafka then parses the key as a javax.crypto.EncryptedPrivateKeyInfo.
#      The unencrypted "BEGIN PRIVATE KEY" produced by `req -nodes` makes the
#      broker exit at startup with:
#        InvalidConfigurationException: Failed to load PEM SSL keystore
#        ... Caused by: java.io.IOException: overrun
#      PKCS#1 ("BEGIN RSA PRIVATE KEY") would fail the same way, so this uses
#      `openssl pkcs8 -topk8` rather than `openssl rsa`.
#
#   3. The encryption must be traditional PKCS#12 PBE (-v1 PBE-SHA1-3DES), not
#      PBES2 (-v2 aes-256-cbc). Kafka decrypts with
#      SecretKeyFactory.getInstance(EncryptedPrivateKeyInfo.getAlgName()), and
#      the JDK has no name for the PBES2 OID, so a PBES2 key fails with
#        NoSuchAlgorithmException: 1.2.840.113549.1.5.13 SecretKeyFactory not
#        available
#      while PBE-SHA1-3DES resolves to PBEWithSHAAnd3-KeyTripleDES-CBC, which
#      SunJCE provides. Kafka only needs to read this key once, at startup.
#
# The Caddy/Grafana pair (localhost-key.pem, localhost.pem) stays unencrypted
# and untouched; only the Kafka-specific bundle is encrypted.
build_kafka_keystore() {
  local password
  password="$(cat "$KAFKA_KEY_PW")"
  local tmp="$KAFKA_KEYSTORE.tmp"
  openssl pkcs8 -topk8 -in "$KEY_FILE" -out "$tmp" \
    -passout "pass:$password" -v1 PBE-SHA1-3DES
  cat "$tmp" "$CRT_FILE" > "$KAFKA_KEYSTORE"
  rm -f "$tmp"
  cp "$CRT_FILE" "$KAFKA_TRUSTSTORE"
  chmod 644 "$KAFKA_KEYSTORE" "$KAFKA_TRUSTSTORE"
}

if [[ -f "$KEY_FILE" && -f "$CRT_FILE" ]]; then
  echo "Dev certificates already exist at $CERT_DIR — skipping generation."
  # The Kafka bundle is derived from the base pair, so it is always rebuilt:
  # that repairs an older checkout whose bundle predates the encrypted-PKCS#8
  # requirement, instead of leaving the broker unable to load its keystore.
  ensure_kafka_passwords
  build_kafka_keystore
  echo "Refreshed the Kafka PEM keystore/truststore from the existing pair."
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

# Kafka's PEM keystore format requires the private key and certificate
# concatenated in ONE file; the truststore is the certificate alone. Caddy and
# Grafana consume the two base files separately, so neither can be reused as-is.
ensure_kafka_passwords
build_kafka_keystore

echo "Generated:"
echo "  $KEY_FILE"
echo "  $CRT_FILE"
echo "  $KAFKA_KEYSTORE (Kafka PEM keystore: key+cert)"
echo "  $KAFKA_TRUSTSTORE (Kafka PEM truststore: cert)"
openssl x509 -noout -ext subjectAltName -in "$CRT_FILE" 2>/dev/null || true
echo "Permissions: $(stat -c %a "$KEY_FILE") $KEY_FILE, $(stat -c %a "$CRT_FILE") $CRT_FILE"
