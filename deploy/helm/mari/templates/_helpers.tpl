{{- define "mari.secretName" -}}
{{- default "mari-secrets" .Values.secrets.existingSecret -}}
{{- end -}}

{{- define "mari.apiImage" -}}
{{- if .Values.api.image.digest -}}
{{ printf "%s@%s" .Values.api.image.repository .Values.api.image.digest }}
{{- else -}}
{{ printf "%s:%s" .Values.api.image.repository .Values.api.image.tag }}
{{- end -}}
{{- end -}}

{{- define "mari.webImage" -}}
{{- if .Values.web.image.digest -}}
{{ printf "%s@%s" .Values.web.image.repository .Values.web.image.digest }}
{{- else -}}
{{ printf "%s:%s" .Values.web.image.repository .Values.web.image.tag }}
{{- end -}}
{{- end -}}
