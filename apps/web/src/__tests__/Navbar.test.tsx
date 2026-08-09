import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import { Navbar } from '@/components/layout/Navbar';
import { useAuthStore } from '@/stores/auth';

const push = vi.fn();
const logout = vi.fn().mockResolvedValue(undefined);

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: push }),
  usePathname: () => '/browse',
}));

function authState(overrides: Record<string, unknown> = {}) {
  useAuthStore.setState({ isAuthenticated: true, logout, ...overrides });
}

beforeEach(() => {
  useAuthStore.setState({ user: null, token: null, isAuthenticated: false });
});

describe('Navbar', () => {
  it('shows sign in links when logged out', () => {
    render(<Navbar />);

    expect(screen.getByRole('link', { name: 'Sign In' })).toHaveAttribute('href', '/login');
    expect(screen.getByRole('link', { name: 'Sign Up' })).toHaveAttribute('href', '/signup');
  });

  it('hides search and profile menu when logged out', () => {
    render(<Navbar />);

    expect(screen.queryByLabelText('Search')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Notifications')).not.toBeInTheDocument();
  });

  it('shows navigation links for authenticated users', () => {
    authState({ user: { firstName: 'Ada', lastName: 'Lovelace', email: 'ada@x.io' } });

    render(<Navbar />);

    expect(screen.getByRole('link', { name: 'Home' })).toHaveAttribute('href', '/browse');
    expect(screen.getByRole('link', { name: 'My List' })).toHaveAttribute('href', '/my-list');
  });

  it('initial profile avatar shows the first letter of the first name', () => {
    authState({ user: { firstName: 'Ada', lastName: 'Lovelace', email: 'ada@x.io' } });

    render(<Navbar />);
    expect(screen.getByText('A')).toBeInTheDocument();
  });

  it('opens search and reports query changes', () => {
    const onSearchChange = vi.fn();
    authState({ user: { firstName: 'Ada', lastName: 'Lovelace', email: 'ada@x.io' } });

    render(<Navbar onSearchChange={onSearchChange} />);

    const searchToggle = screen.getByLabelText('Search');
    fireEvent.click(searchToggle);

    const input = screen.getByPlaceholderText('Titles, people, genres');
    fireEvent.change(input, { target: { value: 'matrix' } });
    expect(onSearchChange).toHaveBeenCalledWith('matrix');
  });
});