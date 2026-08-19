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
{{- define "wildframe.configChecksum" -}}
{{- $ctx := dict "svc" .svc "values" .Values "release" .Release "chart" .Chart }}
{{- toJson $ctx | sha256sum }}
{{- end -}}

{{- define "wildframe.secretChecksum" -}}
{{- $secret := .Values.secrets.existingSecret }}
{{- printf "%s" $secret | sha256sum }}
{{- end -}}