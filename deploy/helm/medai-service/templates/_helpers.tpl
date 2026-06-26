{{/* Generate the full name — honours .Release.Name prefix */}}
{{- define "medai-service.fullname" -}}
{{- if .Values.service.name -}}
{{- printf "%s-%s" .Release.Name .Values.service.name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{/* Common labels applied to every resource */}}
{{- define "medai-service.labels" -}}
app.kubernetes.io/name: {{ .Values.service.name | default .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/component: backend
app.kubernetes.io/part-of: medai-platform
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
{{- end -}}

{{/* Selector labels — stable across deploys, used by Service + HPA */}}
{{- define "medai-service.selectorLabels" -}}
app.kubernetes.io/name: {{ .Values.service.name | default .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}
