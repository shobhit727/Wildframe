/**
 * Shared constants.
 *
 * Scope note: this module holds only the patterns that are actually imported.
 * The other constant bags that used to live here (API_ROUTES, CACHE_KEYS,
 * HTTP_STATUS, ...) were never imported and have been removed, because a
 * central catalogue of values that nothing reads is worse than no catalogue:
 * it looks authoritative while drifting from the code.
 *
 * Specifically, `API_ROUTES` was actively misleading. It advertised paths like
 * `/auth/login` and `/users/me`, while the real client calls the
 * service-prefixed gateway routes `/auth/api/v1/auth/login` and
 * `/users/api/v1/profiles` (see src/api/client.ts). Anyone who had imported it
 * would have shipped a 404.
 *
 * Endpoint paths, query keys and toast copy belong next to the code that uses
 * them, or in a shared module that is genuinely shared.
 */

/**
 * Regular expressions.
 */
export const REGEX = {
  EMAIL: /^[^\s@]+@[^\s@]+\.[^\s@]+$/,
} as const;
