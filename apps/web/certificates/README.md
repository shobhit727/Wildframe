# Dev TLS certificates

This directory holds self-signed certificates for local development **only**.
Certificates are **never committed** — they are generated on demand.

Generate:

```bash
bash scripts/generate-dev-certs.sh
# or
make certs
```

Output (SANs: `DNS:localhost`, `IP:127.0.0.1`, `IP:::1`, `IP:192.168.1.14`):

- `localhost.pem` — certificate (644)
- `localhost-key.pem` — private key (644, non-sensitive sample only)

Both files are ignored by `.gitignore`. Caddy, Grafana, and Kafka mounts
expect them at `apps/web/certificates/` — run the generator before
`docker compose -f deployments/docker-compose.dev.yml up` or `npm run dev`.

Rotate by deleting the files and re-running the script. The private key was
previously committed (issue #788) — that history must be purged separately
with `git filter-repo` or BFG before the repository is considered clean.


---

## Documentation verification — 2026-09-27

Rechecked against the Wildframe source tree at commit `70e04e6417e83a15fe2ca91d993a2c9408a` (the `main` baseline used for this documentation refresh). Repository paths and referenced project structure are maintained against source; when this document conflicts with code, the code is authoritative.
