.PHONY: certs
certs:
	@bash scripts/generate-dev-certs.sh

.PHONY: certs-force
certs-force:
	@rm -f apps/web/certificates/localhost.pem apps/web/certificates/localhost-key.pem
	@bash scripts/generate-dev-certs.sh

.PHONY: help
help:
	@echo "Targets:"
	@echo "  certs        Generate dev TLS certs if missing (SANs localhost,127.0.0.1,::1,192.168.1.14, perms 644)"
	@echo "  certs-force  Regenerate dev TLS certs (delete and recreate)"
