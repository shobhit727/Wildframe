import type { Metadata } from 'next';
import './globals.css';
import { Providers } from './providers';
import { Toaster } from 'sonner';

// The CSP in src/proxy.ts carries a fresh per-request nonce in script-src.
// A statically prerendered route is rendered ONCE at build time, before any
// request (and therefore any nonce) exists, and its cached HTML is then
// replayed verbatim for every request via x-nextjs-cache: HIT. The inline
// RSC bootstrap scripts in that cached HTML can never carry the per-request
// nonce, so script-src 'self' 'nonce-…' blocks them and the client never
// hydrates — a blank page on every route. Force per-request rendering at the
// root so Next applies the nonce from the CSP request header to its own
// scripts. See src/__tests__/csp-nonce-plumbing.test.ts for the regression.
export const dynamic = 'force-dynamic';

export const metadata: Metadata = {
  title: 'Wildframe - Stream Movies & Shows',
  description: 'Watch unlimited movies, TV shows, and more. Stream anywhere, cancel anytime.',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark" suppressHydrationWarning>
      <body className="bg-dark-950 text-white antialiased">
        <Providers>
          {children}
          <Toaster
            theme="dark"
            position="top-right"
            toastOptions={{
              style: {
                background: '#1f2937',
                border: '1px solid #374151',
                color: '#f9fafb',
              },
            }}
          />
        </Providers>
      </body>
    </html>
  );
}
