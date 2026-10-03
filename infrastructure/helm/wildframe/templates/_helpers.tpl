{{- define "wildframe.name" -}}
{{- printf "%s" .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "wildframe.labels" -}}
app.kubernetes.io/name: {{ include "wildframe.name" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "wildframe.dburl" -}}
postgresql://wildframe:$(POSTGRES_PASSWORD)@{{ .Values.infra.postgresHost }}:{{ .Values.infra.postgresPort }}/{{ .db }}
{{- end -}}

{{- define "wildframe.requireManagedExternal" -}}
{{- /* Production enforcement is keyed on .Values.environment, which the
       production values file sets, rather than on a namespace name. Both
       namespace checks are retained as secondary signals, so an operator
       cannot disable every production check by rendering production values
       into a renamed namespace. */}}
{{- $isProd := or (eq .Values.environment "production") (eq .Values.namespace "wildframe-production") (eq .Release.Namespace "wildframe-production") }}
{{- if $isProd }}
{{- if not .Values.external.postgres.enabled }}
{{- fail "production requires external.postgres.enabled=true with managed RDS endpoint (infra.postgresHost)" }}
{{- end }}
{{- if not .Values.external.redis.enabled }}
{{- fail "production requires external.redis.enabled=true with managed ElastiCache endpoint (infra.redisHost)" }}
{{- end }}
{{- if empty .Values.infra.postgresHost }}
{{- fail "production requires infra.postgresHost set to managed RDS endpoint" }}
{{- end }}
{{- if empty .Values.infra.redisHost }}
{{- fail "production requires infra.redisHost set to managed ElastiCache endpoint" }}
{{- end }}
{{- /* `empty` is a length test, not a trim test, so a whitespace-only
       value would sail through and render `key:` as null. */}}
{{- if empty (trim (default "" .Values.secrets.searchCursorSecretKey)) }}
{{- fail "production requires secrets.searchCursorSecretKey set to the key name holding the search cursor secret inside secrets.existingSecret" }}
{{- end }}
{{- if empty .Values.secrets.existingSecret }}
{{- fail "production requires secrets.existingSecret naming the Secret that holds runtime credentials" }}
{{- end }}
{{- $forbiddenPg := list "postgres" "postgres-prod" "postgres-staging" }}
{{- if has .Values.infra.postgresHost $forbiddenPg }}
{{- fail (printf "production infra.postgresHost %q is in-cluster and not allowed; set to RDS endpoint" .Values.infra.postgresHost) }}
{{- end }}
{{- $forbiddenRedis := list "redis" "redis-master" "redis-master-prod" "redis-master-staging" }}
{{- if has .Values.infra.redisHost $forbiddenRedis }}
{{- fail (printf "production infra.redisHost %q is in-cluster and not allowed; set to ElastiCache endpoint" .Values.infra.redisHost) }}
{{- end }}
{{- if and (empty .Values.external.postgres.cidrs) (empty .Values.external.postgres.fqdns) }}
{{- fail "production requires external.postgres.cidrs or fqdns set for NetworkPolicy egress (RDS)" }}
{{- end }}
{{- if and (empty .Values.external.redis.cidrs) (empty .Values.external.redis.fqdns) }}
{{- fail "production requires external.redis.cidrs or fqdns set for NetworkPolicy egress (ElastiCache)" }}
{{- end }}
{{- end }}
{{- end -}}
