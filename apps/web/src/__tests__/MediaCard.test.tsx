import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';

import { MediaCard } from '@/components/browse/MediaCard';
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
    ...overrides,
  };
}

describe('MediaCard', () => {
  it('links to the watch page for the content id', () => {
    render(<MediaCard content={makeContent()} />);

    const link = screen.getByRole('link');
    expect(link).toHaveAttribute('href', '/watch/c1');
  });

  it('labels movies as Film and shows a match percentage on hover', () => {
    render(<MediaCard content={makeContent({ rating: 8.2 })} />);

    expect(screen.getByText('Film')).toBeInTheDocument();
    expect(screen.getByText('82% Match')).toBeInTheDocument();
  });

  it('labels shows as Series', () => {
    render(<MediaCard content={makeContent({ type: 'show' })} />);

    expect(screen.getByText('Series')).toBeInTheDocument();
  });

  it('renders a progress bar when watch progress is provided', () => {
    const { container } = render(<MediaCard content={makeContent()} showProgress={42} />);

    const progressBar = container.querySelector('div[style*="width: 42%"]');
    expect(progressBar).not.toBeNull();
  });

  it('renders caption with title and year in grid views', () => {
    render(<MediaCard content={makeContent()} showCaption />);

    expect(screen.getAllByText('Die Hard').length).toBeGreaterThan(0);
    expect(screen.getByText('1988 · ★ 8.2')).toBeInTheDocument();
  });
});