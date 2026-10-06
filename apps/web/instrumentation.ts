/**
 * Next.js instrumentation hook.
 *
 * Keep this hook runtime-neutral: Next evaluates instrumentation in both
 * Node.js and Edge contexts. Development TLS trust is supplied by the
 * process environment instead of importing Node-only modules here.
 */
export async function register() {
  // Intentionally empty.
}
