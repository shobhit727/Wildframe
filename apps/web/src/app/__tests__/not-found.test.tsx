import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';

import NotFound from '@/app/not-found';

describe('NotFound (404)', () => {
  it('tells the visitor the page does not exist', () => {
    render(<NotFound />);

    expect(screen.getByRole('heading', { level: 1, name: '404' })).toBeInTheDocument();
    expect(screen.getByText('Page not found')).toBeInTheDocument();
  });

  it('offers a single recovery link back into the catalogue', () => {
    render(<NotFound />);

    const links = screen.getAllByRole('link');
    expect(links).toHaveLength(1);
    expect(links[0]).toHaveTextContent('Go to Browse');
    expect(links[0]).toHaveAttribute('href', '/browse');
  });

  it('does not render a dead-end dead button', () => {
    render(<NotFound />);

    // A 404 that offers no action leaves the visitor stuck; the page must be
    // navigable without a reload.
    expect(screen.queryByRole('button')).toBeNull();
  });
});
