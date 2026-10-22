/**
 * Next.js instrumentation hook (server boot).
 *
 * Dev servers fetch backend services over HTTPS terminated by the project's
 * self-signed certificate (apps/web/certificates/localhost.pem). Next spawns
 * its server worker with a sanitized environment, so NODE_EXTRA_CA_CERTS does
 * not reach it — instead, load the cert into Node's root store here.
 *
 * Silently no-ops in production/CI where the cert file is absent.
 */
export async function register() {
  if (process.env.NEXT_RUNTIME !== "nodejs") return;
  try {
    const fs = await import("node:fs");
    const path = await import("node:path");
    const certPath = path.join(process.cwd(), "certificates", "localhost.pem");
    if (!fs.existsSync(certPath)) return;
    const pem = fs.readFileSync(certPath, "utf8");
    const certs = pem
      .split(/(?=-----BEGIN CERTIFICATE-----)/)
      .map((c) => c.trim())
      .filter((c) => c.startsWith("-----BEGIN CERTIFICATE-----"));
    if (certs.length === 0) return;
    const tls = await import("node:tls");
    for (const cert of certs) {
      tls.rootCertificates.push(cert);
    }
    console.log(`[instrumentation] loaded ${certs.length} dev CA cert(s) into trust store`);
  } catch {
    // Never block server startup over trust-store customization.
  }
}
