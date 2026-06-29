import type { Metadata } from 'next';
import './globals.css';
import { Providers } from './providers';
import { Toaster } from 'sonner';

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
    <html lang="en" suppressHydrationWarning>
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
