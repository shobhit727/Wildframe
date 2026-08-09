# Admin Configuration Audit

> **Audit note: created by ChatGPT during the Wildframe code audit.**

## Finding

`services/admin-service/app/services/admin.py` writes the complete configuration value into the audit-log detail field:

`value={value}`

The same service returns configuration values from admin endpoints. The code does not establish that configuration values are non-sensitive.

## Impact

If an administrator stores a secret, token, credential, connection string, or other sensitive value in system configuration, the value can be duplicated into audit logs and exposed to anyone with audit-log access. Audit logs generally have a longer retention period and broader operational visibility than the original secret.

The admin routes also hard-code `0.0.0.0` as the recorded IP address for moderation/configuration operations, which destroys the real client attribution needed for a useful audit trail.

## Required fix

- Classify configuration keys as public/non-sensitive vs sensitive.
- Never persist sensitive values in audit-log detail; record only the key and an explicit redaction marker.
- Consider masking sensitive configuration values in GET/list responses.
- Capture the real request client IP, accounting for a trusted proxy configuration rather than blindly trusting forwarded headers.
- Add tests proving secrets never appear in audit records or API responses where they should be masked.

## Status

**High-confidence security/privacy finding.** Requires a deliberate configuration-secret policy before changing response behavior.
