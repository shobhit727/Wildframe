#!/usr/bin/env bash
# Generate self-signed development TLS certificates for Wildframe.
# Output: apps/web/certificates/localhost.pem and localhost-key.pem
# SANs: DNS:localhost, IP:127.0.0.1, IP:::1, plus this host's own global-scope
#       addresses, discovered at run time (see build_san_list).
# Permissions: 644 (Caddy/Grafana containers read as non-root)
# Idempotent: skips if both files already exist.
#
# Re-run after changing networks: the cert is only valid for the addresses the
# host had when it was generated, and the script will not overwrite an
# existing pair.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CERT_DIR="$REPO_ROOT/apps/web/certificates"
KEY_FILE="$CERT_DIR/localhost-key.pem"
CRT_FILE="$CERT_DIR/localhost.pem"
KAFKA_KEYSTORE="$CERT_DIR/kafka-keystore.pem"
KAFKA_TRUSTSTORE="$CERT_DIR/kafka-truststore.pem"
KAFKA_KEYSTORE_PW="$CERT_DIR/kafka_keystore_password"
KAFKA_KEY_PW="$CERT_DIR/kafka_key_password"
METRICS_TOKEN_FILE="$REPO_ROOT/infrastructure/prometheus/metrics_bearer_token"

mkdir -p "$CERT_DIR"
mkdir -p "$(dirname "$METRICS_TOKEN_FILE")"

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

# Prometheus reads the /metrics bearer token from a FILE (bearer_token_file),
# while the seven services that gate /metrics read the same value from
# METRICS_TOKEN in their environment. Compose interpolates both from one
# variable, so this file and settings.METRICS_TOKEN cannot drift.
#
# It is written on every run, not derived from the certificate pair, so the
# "already exists" path must call it too -- otherwise a checkout that already
# has certs never gets a token and Prometheus silently 401s once
# ENVIRONMENT=production. Dev-only default, matching the Kafka passwords
# above; the file is gitignored and never committed.
ensure_metrics_token() {
  local token="${METRICS_TOKEN:-wildframe-metrics-dev}"
  printf '%s' "$token" > "$METRICS_TOKEN_FILE"
  chmod 644 "$METRICS_TOKEN_FILE"
}

if [[ -f "$KEY_FILE" && -f "$CRT_FILE" ]]; then
  echo "Dev certificates already exist at $CERT_DIR — skipping generation."
  # The Kafka bundle is derived from the base pair, so it is always rebuilt:
  # that repairs an older checkout whose bundle predates the encrypted-PKCS#8
  # requirement, instead of leaving the broker unable to load its keystore.
  ensure_kafka_passwords
  build_kafka_keystore
  ensure_metrics_token
  echo "Refreshed the Kafka PEM keystore/truststore from the existing pair."
  echo "  $KEY_FILE"
  echo "  $CRT_FILE"
  echo "Delete them to regenerate."
  exit 0
fi

echo "Generating self-signed dev certificates..."

# The addresses this host can be reached on.
#
# The SAN list used to carry one developer's LAN address as a literal in this
# file. A developer's address changes when they move networks, and on every
# other machine that literal was simply wrong -- which made the certificate
# invalid for the address the browser was actually connecting to, so the
# browser refused the connection and the failure looked like a broken stack
# rather than a wrong certificate. It was also incomplete on the machine it was
# written on: a host with two IPv4 addresses had only one of them covered, so
# a browser connecting via the other one failed hostname verification. The
# list is therefore discovered at run time from the host's own interfaces.
#
# Detection is deliberately tolerant. It tries the tools that exist on the
# platform and yields quietly if none of them do, because a certificate that
# covers loopback is far better than a script that refuses to run. The
# loopback entries are always present regardless, so the local dev stack works
# even when detection finds nothing.
detect_host_addresses() {
  local raw=""

  if command -v ip >/dev/null 2>&1; then
    # Linux (iproute2): "inet 203.0.113.10/24 brd ... scope global"
    raw="$(ip -o addr show scope global 2>/dev/null |
      awk '{for (i = 1; i <= NF; i++) if ($i == "inet" || $i == "inet6") {split($(i + 1), a, "/"); print a[1]}}')"
  fi

  # Having the tool is not the same as having an address: a host can have
  # `ip` and no global-scope address, so an empty result is not the end of the
  # search. Try the next tool.
  if [[ -z "$raw" ]] && command -v ifconfig >/dev/null 2>&1; then
    # macOS and the BSDs
    raw="$(ifconfig 2>/dev/null |
      awk '/inet /{split($2, a, "/"); print a[1]} /inet6 /{split($2, a, "/"); print a[1]}')"
  fi

  if [[ -z "$raw" ]] && command -v hostname >/dev/null 2>&1; then
    # Minimal Linux images without iproute2. `hostname -I` prints the
    # addresses space-separated on a single line, so split them here.
    raw="$(hostname -I 2>/dev/null | tr ' ' '\n')"
  fi

  printf '%s\n' "$raw"
}

# Build the subjectAltName value.
#
# Always covers DNS:localhost and both loopback addresses, then adds this
# host's own reachable addresses. DEV_CERT_EXTRA_IPS lets a developer add an
# address that detection cannot see -- a VPN alias, or a second NIC the
# platform tool reports differently. It takes a comma- or space-separated list.
build_san_list() {
  local -a candidates=()
  local san="DNS:localhost,IP:127.0.0.1,IP:::1"
  local addr
  local seen="|localhost|127.0.0.1|::1|"

  while read -r addr; do
    [[ -n "$addr" ]] && candidates+=("$addr")
  done < <(detect_host_addresses; printf '%s\n' "${DEV_CERT_EXTRA_IPS:-}" | tr ', ' '\n\n')

  for addr in "${candidates[@]}"; do
    # An IPv6 zone index (fe80::1%eth0) is not valid in a SAN.
    addr="${addr%%%*}"
    case "$addr" in
      "" | "::" | "0.0.0.0" | "::1" | 127.* | 169.254.* | fe80:*) continue ;;
    esac
    # Already covered, or a duplicate of an earlier interface.
    [[ "$seen" == *"|$addr|"* ]] && continue
    seen="$seen$addr|"
    san="$san,IP:$addr"
  done

  printf '%s' "$san"
}

SAN_LIST="$(build_san_list)"

# Try modern openssl with -addext first; fall back to config file for older versions.
if openssl req -x509 -newkey rsa:2048 \
  -keyout "$KEY_FILE" -out "$CRT_FILE" \
  -days 365 -nodes \
  -subj "/CN=localhost" \
  -addext "subjectAltName=$SAN_LIST" 2>/dev/null; then
  :
else
  echo "openssl -addext not supported, using config fallback..."
  TMP_CNF="$(mktemp)"
  cat > "$TMP_CNF" <<CNF
[req]
distinguished_name=req_distinguished_name
x509_extensions=v3_req
prompt=no
[req_distinguished_name]
CN=localhost
[v3_req]
subjectAltName=$SAN_LIST
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
ensure_metrics_token

echo "Generated:"
echo "  $KEY_FILE"
echo "  $CRT_FILE"
echo "  $KAFKA_KEYSTORE (Kafka PEM keystore: key+cert)"
echo "  $KAFKA_TRUSTSTORE (Kafka PEM truststore: cert)"
openssl x509 -noout -ext subjectAltName -in "$CRT_FILE" 2>/dev/null || true
echo "Permissions: $(stat -c %a "$KEY_FILE") $KEY_FILE, $(stat -c %a "$CRT_FILE") $CRT_FILE"
