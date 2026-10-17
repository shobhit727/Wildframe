import { describe, expect, it } from 'vitest';

import RootLayout, { metadata } from '@/app/layout';

/**
 * The root layout's <html>/<body> shell cannot be rendered in jsdom — the
 * document already exists and React refuses to nest a second one — so this
 * covers the part of the layout that is genuinely a page concern: the SEO
 * metadata Next.js turns into the document head.
 */
describe('RootLayout metadata (/ layout)', () => {
  it('exposes a document title for the browser tab and SERP', () => {
    expect(metadata.title).toBe('Wildframe - Stream Movies & Shows');
  });

  it('exposes a search-description blurb', () => {
    expect(metadata.description).toBe(
      'Watch unlimited movies, TV shows, and more. Stream anywhere, cancel anytime.',
    );
  });

  it('exports a root layout component', () => {
    expect(typeof RootLayout).toBe('function');
  });
});
