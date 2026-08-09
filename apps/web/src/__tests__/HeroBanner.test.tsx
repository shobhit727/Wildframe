import { describe, expect, it } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import { HeroBanner } from '@/components/browse/HeroBanner';
import type { Content } from '@/types';

function makeContent(overrides: Partial<Content> = {}): Content {
  return {
    id: 'c1',
    title: 'Die Hard',
    description: 'A cop fights terrorists.',
    genre: 'Action',
    poster: '',
    backdrop: '',
    duration: 132,
    releaseDate: '1988-07-15',
    rating: 8.2,
    type: 'movie',
    maturityRating: 'R',
    isHd: true,
    ...overrides,
  };
}

describe('HeroBanner', () => {
  it('renders nothing when there are no items', () => {
    const { container } = render(<HeroBanner items={[]} />);
    expect(container.firstChild).toBeNull();
  });

  it('shows the active title, play CTAs and match percentage', () => {
    render(<HeroBanner items={[makeContent()]} />);

    expect(screen.getByRole('heading', { level: 1, name: 'Die Hard' })).toBeInTheDocument();
    expect(screen.getByText('82% Match')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Play' })).toHaveAttribute('href', '/watch/c1');
  });

  it('renders dot indicators to switch slides', () => {
    render(
      <HeroBanner
        items={[makeContent({ id: 'c1', title: 'First' }), makeContent({ id: 'c2', title: 'Second' })]}
      />,
    );

    const dots = screen.getAllByRole('button', { name: /^Go to slide/ });
    expect(dots).toHaveLength(2);
  });

  it('formats the first slide but allows switching via dot', () => {
    render(
      <HeroBanner
        items={[makeContent({ id: 'c1', title: 'First' }), makeContent({ id: 'c2', title: 'Second' })]}
      />,
    );

    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('First');

    fireEvent.click(screen.getByRole('button', { name: 'Go to slide 2' }));

    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Second');
  });
});