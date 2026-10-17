import { describe, expect, it } from 'vitest';
import { screen } from '@testing-library/react';
import { render } from '@testing-library/react';

import HomePage from '@/app/page';

describe('HomePage (/)', () => {
  it('renders the hero heading and its primary calls to action', () => {
    render(<HomePage />);

    expect(
      screen.getByRole('heading', { level: 1, name: 'Stories that pull you in.' }),
    ).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /start exploring/i })).toHaveAttribute(
      'href',
      '/signup',
    );
    expect(screen.getByRole('link', { name: 'Browse titles' })).toHaveAttribute('href', '/browse');
  });

  it('exposes sign-in and sign-up entry points in the top navigation', () => {
    render(<HomePage />);

    expect(screen.getByRole('link', { name: 'Sign In' })).toHaveAttribute('href', '/login');
    expect(screen.getByRole('link', { name: 'Get Started' })).toHaveAttribute('href', '/signup');
  });

  it('renders one card per feature with its eyebrow, title and description', () => {
    render(<HomePage />);

    const features = [
      {
        eyebrow: 'EVERY SCREEN',
        title: 'Your library, wherever you are.',
        copy: /continuous viewing experience/i,
      },
      {
        eyebrow: 'SMART DISCOVERY',
        title: 'Find something worth watching.',
        copy: /media-first interface/i,
      },
      {
        eyebrow: 'YOUR LIST',
        title: 'Keep the good stuff close.',
        copy: /one click away/i,
      },
    ];

    for (const feature of features) {
      const heading = screen.getByRole('heading', { level: 3, name: feature.title });
      expect(heading).toBeInTheDocument();
      // The eyebrow and body copy live inside the same <article> as the heading.
      const article = heading.closest('article');
      expect(article).not.toBeNull();
      expect(article).toHaveTextContent(feature.eyebrow);
      expect(article).toHaveTextContent(feature.copy);
    }
  });

  it('renders every FAQ as a closed, keyboard-operable native disclosure', () => {
    const { container } = render(<HomePage />);

    const expected = [
      ['What is Wildframe?', 'discovery, playback, accounts'],
      ['Is Wildframe production-ready?', 'actively under development'],
      ['Where can I watch?', 'primary interface'],
      ['Can I save titles?', 'My List experience'],
    ] as const;

    const details = Array.from(container.querySelectorAll('details'));
    expect(details).toHaveLength(expected.length);

    for (const [index, [question, answerFragment]] of expected.entries()) {
      const el = details[index];
      // Must start collapsed — a permanently-open disclosure would defeat the
      // point of the accordion and dump all four answers on screen at once.
      expect(el).not.toHaveAttribute('open');

      const summary = el.querySelector('summary');
      expect(summary).not.toBeNull();
      expect(summary).toHaveTextContent(question);

      // The answer is nested inside the same <details>, which is what makes it
      // toggle with the question rather than being permanently visible copy.
      expect(el).toHaveTextContent(new RegExp(answerFragment, 'i'));
    }
  });

  it('states the project is not production-ready rather than claiming otherwise', () => {
    const { container } = render(<HomePage />);

    const details = Array.from(container.querySelectorAll('details')).find((d) =>
      d.textContent?.includes('Is Wildframe production-ready?'),
    );
    expect(details).toBeDefined();
    expect(details?.textContent).toMatch(/Not yet/i);
    expect(details?.textContent).toMatch(/actively under development/i);
  });

  it('closes the page with a create-account CTA and a duplicate footer entry point', () => {
    render(<HomePage />);

    expect(
      screen.getByRole('heading', { level: 2, name: 'Ready to explore?' }),
    ).toBeInTheDocument();

    // "Create account" intentionally appears twice (final CTA + footer); both
    // must point at /signup so the sign-up path is reachable from either spot.
    const createLinks = screen.getAllByRole('link', { name: 'Create account' });
    expect(createLinks).toHaveLength(2);
    for (const link of createLinks) {
      expect(link).toHaveAttribute('href', '/signup');
    }
    expect(screen.getByRole('link', { name: 'Sign in' })).toHaveAttribute('href', '/login');
  });
});
