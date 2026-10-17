import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

import ErrorPage from '@/app/error';

type ErrorPageProps = React.ComponentProps<typeof ErrorPage>;

function renderError(overrides: Partial<ErrorPageProps> = {}) {
  const props: ErrorPageProps = {
    error: new Error('Failed to load catalogue') as ErrorPageProps['error'],
    reset: vi.fn(),
    ...overrides,
  };
  return { props, ...render(<ErrorPage {...props} />) };
}

describe('error boundary page', () => {
  it('surfaces the underlying error message to the user', () => {
    renderError();

    expect(screen.getByRole('heading', { level: 1, name: 'Oops' })).toBeInTheDocument();
    expect(screen.getByText('Failed to load catalogue')).toBeInTheDocument();
  });

  it('falls back to generic copy when the error carries no message', () => {
    renderError({ error: new Error('') as ErrorPageProps['error'] });

    expect(screen.getByText('Something went wrong')).toBeInTheDocument();
  });

  it('shows the digest only when the server supplied one', () => {
    const { unmount } = renderError({
      error: Object.assign(new Error('Boom'), { digest: 'abc123' }),
    });
    expect(screen.getByText(/Error ID: abc123/)).toBeInTheDocument();
    unmount();

    renderError();
    expect(screen.queryByText(/Error ID:/)).toBeNull();
  });

  it('re-runs the failed segment when the visitor retries', () => {
    const { props } = renderError();

    const retry = screen.getByRole('button', { name: 'Try again' });
    expect(retry).toBeEnabled();
    expect(props.reset).not.toHaveBeenCalled();

    fireEvent.click(retry);

    // Next.js re-mounts the subtree only when reset() runs, so the button must
    // be wired to the injected handler rather than a no-op local state change.
    expect(props.reset).toHaveBeenCalledTimes(1);
  });
});
