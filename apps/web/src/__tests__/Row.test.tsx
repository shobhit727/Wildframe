import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';

import { Row } from '@/components/browse/Row';
import type { Content } from '@/types';

function makeContent(id: string, overrides: Partial<Content> = {}): Content {
  return {
    id,
    title: `Title ${id}`,
    description: '',
    genre: 'Action',
    poster: '',
    backdrop: '',
    duration: 100,
    releaseDate: '2020-01-01',
    rating: 8,
    type: 'movie',
    ...overrides,
  };
}

describe('Row', () => {
  it('renders the row title', () => {
    render(<Row title="Trending Now" items={[makeContent('a')]} />);

    expect(screen.getByRole('heading', { name: 'Trending Now' })).toBeInTheDocument();
  });

  it('renders one card per item linking to its watch page', () => {
    render(<Row title="Row" items={[makeContent('a'), makeContent('b')]} />);

    expect(screen.getByRole('link', { name: /Title a/ })).toHaveAttribute('href', '/watch/a');
    expect(screen.getByRole('link', { name: /Title b/ })).toHaveAttribute('href', '/watch/b');
  });

  it('renders nothing for an empty list', () => {
    const { container } = render(<Row title="Empty" items={[]} />);
    expect(container.firstChild).toBeNull();
  });

  it('uses the backdrop variant when requested', () => {
    const { container } = render(<Row title="Row" items={[makeContent('a')]} variant="backdrop" />);
    expect(container.querySelector('.aspect-video')).not.toBeNull();
  });
});