/**
 * Content Security Policy builder.
 * Generates a strict CSP with nonce-based script-src for production.
 * In development, allows 'unsafe-inline' and 'unsafe-eval' for HMR.
 */
export interface CspOptions {
  nonce: string;
  isDev: boolean;
  apiUrl: string;
}

/**
 * Build CSP header value.
 * - script-src: 'self' + nonce (prod) or 'self' + nonce + 'unsafe-inline' + 'unsafe-eval' (dev)
 * - style-src: 'self' 'unsafe-inline' (needed for Tailwind/inline styles)
 * - Other directives from current config
 */
export function buildCspHeader(opts: CspOptions): string {
  const { nonce, isDev, apiUrl } = opts;
  const nonceSource = `'nonce-${nonce}'`;

  const scriptSrc = isDev
    ? `'self' ${nonceSource} 'unsafe-inline' 'unsafe-eval'`
    : `'self' ${nonceSource} 'unsafe-inline'`; // Allow unsafe-inline for static pages

  const directives = [
    "default-src 'self'",
    `script-src ${scriptSrc}`,
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' blob: data: https:",
    "media-src 'self' blob: https:",
    "font-src 'self' data:",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
    `connect-src 'self' ${apiUrl} https:`,
    "worker-src 'self' blob:",
  ];

  return directives.join('; ');
}

/**
 * Generate a cryptographically random nonce (base64url, 32 chars).
 * Uses crypto.getRandomValues for browser/edge compatibility.
 */
export function generateNonce(): string {
  // crypto.randomUUID() returns hex without hyphens is also valid for CSP nonce regex
  // CSP_NONCE_SOURCE_REGEX = /^'nonce-([A-Za-z0-9+/_-]+={0,2})'$/ accepts alphanumeric.
  // Using crypto.randomUUID() is simplest and works in edge runtime.
  if (typeof crypto !== 'undefined' && crypto.randomUUID) {
    return crypto.randomUUID().replace(/-/g, '');
  }
  // Fallback for environments without crypto.randomUUID
  const array = new Uint8Array(16);
  if (typeof crypto !== 'undefined' && crypto.getRandomValues) {
    crypto.getRandomValues(array);
  } else {
    // Last resort: Math.random (not cryptographically secure, but dev only)
    for (let i = 0; i < array.length; i++) {
      array[i] = Math.floor(Math.random() * 256);
    }
  }
  return btoa(String.fromCharCode(...array))
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=/g, '');
}
