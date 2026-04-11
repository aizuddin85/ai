{{/*
Expand the name of the chart.
*/}}
{{- define "aks-health.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this.
*/}}
{{- define "aks-health.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart label value: <chart-name>-<chart-version>
*/}}
{{- define "aks-health.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels applied to every resource.
*/}}
{{- define "aks-health.labels" -}}
helm.sh/chart: {{ include "aks-health.chart" . }}
{{ include "aks-health.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels – used in matchLabels and pod template labels.
*/}}
{{- define "aks-health.selectorLabels" -}}
app.kubernetes.io/name: {{ include "aks-health.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
ServiceAccount name.
*/}}
{{- define "aks-health.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "aks-health.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Name of the Secret that holds Azure credentials.
Uses the existingSecret when provided, otherwise the chart-managed secret.
*/}}
{{- define "aks-health.secretName" -}}
{{- if .Values.backend.existingSecret }}
{{- .Values.backend.existingSecret }}
{{- else }}
{{- include "aks-health.fullname" . }}
{{- end }}
{{- end }}

{{/*
Returns true when the chart should create its own Secret
(i.e. existingSecret is not set AND at least one credential is non-empty).
*/}}
{{- define "aks-health.createSecret" -}}
{{- if not .Values.backend.existingSecret }}
{{- if or .Values.backend.credentials.azureClientId
          .Values.backend.credentials.azureClientSecret
          .Values.backend.credentials.azureFoundryApiKey }}
{{- "true" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Returns true when the backend pod should mount a credentials secret
(either chart-managed or external).
*/}}
{{- define "aks-health.mountSecret" -}}
{{- if or .Values.backend.existingSecret
          .Values.backend.credentials.azureClientId
          .Values.backend.credentials.azureClientSecret
          .Values.backend.credentials.azureFoundryApiKey }}
{{- "true" }}
{{- end }}
{{- end }}

{{/*
TLS secret name for Ingress.
*/}}
{{- define "aks-health.tlsSecretName" -}}
{{- if .Values.ingress.tls.secretName }}
{{- .Values.ingress.tls.secretName }}
{{- else }}
{{- printf "%s-tls" (include "aks-health.fullname" .) }}
{{- end }}
{{- end }}
