#!/usr/bin/env bash
#
# Dev-only entrypoint wrapper for the kafka service.
#
# Mounted read-only by deployments/docker-compose.dev.yml and referenced from
# that file's `entrypoint:` key. It exists to solve exactly one problem, then
# hands control back to the image's own scripts in the image's own order.
#
# WHY: the broker's SASL credentials have to come from a *static* JAAS
# configuration -- a "KafkaServer" section plus the JVM system property
# java.security.auth.login.config. Two independent facts force that here:
#
#   1. The broker-to-controller channel (BrokerToControllerChannelManagerImpl)
#      resolves its credentials from the static configuration and ignores the
#      listener-scoped KAFKA_LISTENER_NAME_*_SASL_JAAS_CONFIG settings. Without
#      it the broker crash-loops at startup with
#        IllegalArgumentException: Could not find a 'KafkaServer' or
#        'sasl_ssl.KafkaServer' entry in the JAAS configuration.
#
#   2. Listener-scoped credentials are not reachable through the environment in
#      this stack at all. cp-kafka's configure step lowercases every KAFKA_*
#      variable, so KAFKA_LISTENER_NAME_SASL_SSL_PLAIN_SASL_JAAS_CONFIG lands in
#      kafka.properties as `listener.name.sasl.ssl.plain.sasl.jaas.config`, which
#      never matches the listener actually named `SASL_SSL` (Kafka matches
#      listener names case-sensitively). Verified: with only that property set,
#      a client authenticating as `admin` succeeded while all 15 service users
#      were rejected -- every listener credential was falling back to this file
#      anyway. So this file is the single source of listener credentials too, and
#      the misleading listener-scoped properties were removed rather than left
#      in place looking effective.
#
# The file is GENERATED rather than committed so the credentials keep exactly
# one source of truth: the environment. Each service's username/password is read
# from the same variables, with the same defaults, that the service itself uses
# in the compose file, so the broker and the services cannot drift. A committed
# file would be a second copy of those secrets that can silently diverge.
#
# Only the broker's own identity, the admin super user and the service users are
# written below. The truststore password is intentionally absent: Kafka rejects
# ssl.keystore.password/ssl.truststore.password outright when the keystore type
# is PEM ("only key password may be specified").
#
# NOTE: no `set -e` here on purpose. /etc/confluent/docker/ensure does not check
# `cub zk-ready`'s exit status, and the image's launch script dereferences
# $KAFKA_JMX_OPTS unguarded, so `set -u` would abort the broker. Keep the
# image's own control flow intact and call its scripts directly.
#
. /etc/confluent/docker/bash-config

# "<username env var> <password env var>" per service, matching the
# *_KAFKA_USERNAME / *_KAFKA_PASSWORD variables the compose file gives each
# service. Order is cosmetic; JAAS option order is not significant.
SERVICE_CREDENTIAL_VARS=(
  "API_GATEWAY_KAFKA_USERNAME API_GATEWAY_KAFKA_PASSWORD"
  "AUTH_SERVICE_KAFKA_USERNAME AUTH_SERVICE_KAFKA_PASSWORD"
  "USER_SERVICE_KAFKA_USERNAME USER_SERVICE_KAFKA_PASSWORD"
  "CONTENT_SERVICE_KAFKA_USERNAME CONTENT_SERVICE_KAFKA_PASSWORD"
  "STREAMING_SERVICE_KAFKA_USERNAME STREAMING_SERVICE_KAFKA_PASSWORD"
  "SEARCH_SERVICE_KAFKA_USERNAME SEARCH_SERVICE_KAFKA_PASSWORD"
  "RECOMMENDATION_SERVICE_KAFKA_USERNAME RECOMMENDATION_SERVICE_KAFKA_PASSWORD"
  "BILLING_SERVICE_KAFKA_USERNAME BILLING_SERVICE_KAFKA_PASSWORD"
  "ANALYTICS_SERVICE_KAFKA_USERNAME ANALYTICS_SERVICE_KAFKA_PASSWORD"
  "NOTIFICATION_SERVICE_KAFKA_USERNAME NOTIFICATION_SERVICE_KAFKA_PASSWORD"
  "CREATORS_SERVICE_KAFKA_USERNAME CREATORS_SERVICE_KAFKA_PASSWORD"
  "MODERATION_SERVICE_KAFKA_USERNAME MODERATION_SERVICE_KAFKA_PASSWORD"
  "UPLOADS_SERVICE_KAFKA_USERNAME UPLOADS_SERVICE_KAFKA_PASSWORD"
  "MEDIA_PIPELINE_KAFKA_USERNAME MEDIA_PIPELINE_KAFKA_PASSWORD"
  "ADMIN_SERVICE_KAFKA_USERNAME ADMIN_SERVICE_KAFKA_PASSWORD"
)

# Contains passwords: keep it owner-only.
#
# JAAS syntax note: the option list must be terminated with a `;` BEFORE the
# closing `};`. Omitting it makes the parser treat `};` as one more option key
# and the broker dies with
#   SecurityException: java.io.IOException: Configuration Error:
#       Line N: expected [option key]
# so the trailing semicolon is emitted with the final option below.
umask 077
options="  username=\"$WF_INTER_BROKER_USER\"
  password=\"$WF_INTER_BROKER_PASSWORD\"
  user_$WF_INTER_BROKER_USER=\"$WF_INTER_BROKER_PASSWORD\"
  user_admin=\"$WF_ADMIN_PASSWORD\""
for entry in "${SERVICE_CREDENTIAL_VARS[@]}"; do
  # shellcheck disable=SC2086
  set -- $entry
  options+="
  user_${!1}=\"${!2}\""
done
printf 'KafkaServer {\n  org.apache.kafka.common.security.plain.PlainLoginModule required\n%s;\n};\n' \
  "$options" > /etc/kafka/kafka-server-jaas.conf

echo "===> Wrote /etc/kafka/kafka-server-jaas.conf with $(grep -c 'user_' /etc/kafka/kafka-server-jaas.conf) user entries ==="

echo "===> Configuring ... "
/etc/confluent/docker/configure

# cp-kafka's configure step unconditionally exports KAFKA_SSL_KEYSTORE_PASSWORD
# from the $KAFKA_SSL_KEYSTORE_CREDENTIALS file whenever SSL is enabled, so
# ssl.keystore.password always lands in kafka.properties. Kafka rejects exactly
# that combination when the keystore is PEM:
#
#   InvalidConfigurationException: SSL key store password cannot be specified
#   with PEM format, only key password may be specified
#
# thrown while building the broker-to-controller channel, so the broker exits
# during startup. There is no environment-only way to avoid it: configure
# hard-fails (dub ensure KAFKA_SSL_KEYSTORE_CREDENTIALS) if the credentials var
# is unset, and reads the password in if it is set. So drop the one invalid
# property here, after configure has written the file and before the broker
# reads it. This removes an unusable setting, it does not relax a check.
#
# Fail loudly rather than silently doing nothing, so a future image change that
# stops injecting the property is noticed instead of hidden.
KAFKA_PROPERTIES=/etc/kafka/kafka.properties
if grep -q '^ssl\.keystore\.password=' "$KAFKA_PROPERTIES"; then
    echo "===> Removing ssl.keystore.password (invalid for a PEM keystore) ==="
    grep -v '^ssl\.keystore\.password=' "$KAFKA_PROPERTIES" > "$KAFKA_PROPERTIES.tmp" \
        && mv "$KAFKA_PROPERTIES.tmp" "$KAFKA_PROPERTIES"
else
    echo "===> WARNING: ssl.keystore.password not present; expected cp-kafka to" \
         "inject it from $KAFKA_SSL_KEYSTORE_CREDENTIALS. Verify the broker" \
         "still starts with a PEM keystore. ==="
fi

echo "===> Running preflight checks ... "
/etc/confluent/docker/ensure

echo "===> Launching ... "
exec /etc/confluent/docker/launch
