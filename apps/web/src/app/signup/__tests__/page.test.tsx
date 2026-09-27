import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import SignupPage from '@/app/signup/page';
import { useAuthStore } from '@/stores/auth';

const push = vi.fn();

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push, replace: vi.fn(), refresh: vi.fn() }),
}));

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

const register = vi.fn();

const VALID = {
  firstName: 'Ada',
  lastName: 'Lovelace',
  email: 'ada@wildframe.test',
  // 13 chars, mixed classes — satisfies both length and composition rules.
  password: 'Passw0rd!abc',
};

function fill(overrides: Partial<typeof VALID> & { confirmPassword?: string } = {}) {
  const values = { ...VALID, ...overrides };
  fireEvent.change(screen.getByLabelText('First name'), { target: { value: values.firstName } });
  fireEvent.change(screen.getByLabelText('Last name'), { target: { value: values.lastName } });
  fireEvent.change(screen.getByLabelText('Email'), { target: { value: values.email } });
  fireEvent.change(screen.getByLabelText('Password'), { target: { value: values.password } });
  fireEvent.change(screen.getByLabelText('Confirm password'), {
    target: { value: values.confirmPassword ?? values.password },
  });
}

function submit() {
  fireEvent.click(screen.getByRole('button', { name: 'Create Account' }));
}

beforeEach(() => {
  push.mockReset();
  register.mockReset().mockResolvedValue(undefined);
  useAuthStore.setState({
    user: null,
    token: null,
    isAuthenticated: false,
    isLoading: false,
    register,
  });
});

describe('SignupPage (/signup)', () => {
  it('requires a first and last name', () => {
    render(<SignupPage />);
    submit();

    expect(screen.getByText('First name is required')).toBeInTheDocument();
    expect(screen.getByText('Last name is required')).toBeInTheDocument();
    expect(register).not.toHaveBeenCalled();
  });

  it('treats whitespace-only names as missing', () => {
    render(<SignupPage />);
    fill({ firstName: '   ', lastName: '\t' });
    submit();

    // A name of spaces sails past a naive `!value` check and creates an account
    // whose profile renders as a blank.
    expect(screen.getByText('First name is required')).toBeInTheDocument();
    expect(screen.getByText('Last name is required')).toBeInTheDocument();
    expect(register).not.toHaveBeenCalled();
  });

  it('rejects a malformed email address', () => {
    render(<SignupPage />);
    fill({ email: 'ada@' });
    submit();

    expect(screen.getByText('Enter a valid email address')).toBeInTheDocument();
    expect(register).not.toHaveBeenCalled();
  });

  it('enforces the twelve character password minimum', () => {
    render(<SignupPage />);
    fill({ password: 'Sh0rt!abc' });
    submit();

    expect(screen.getByText('Password must be at least 12 characters')).toBeInTheDocument();
    expect(register).not.toHaveBeenCalled();
  });

  it('requires at least two character classes in the password', () => {
    render(<SignupPage />);
    fill({ password: 'aaaaaaaaaaaaa' });
    submit();

    expect(
      screen.getByText('Password must mix at least two of: letters, numbers, symbols'),
    ).toBeInTheDocument();
    expect(register).not.toHaveBeenCalled();
  });

  it('accepts a long single-class-plus-symbol password', async () => {
    render(<SignupPage />);
    fill({ password: 'aaaaaaaaaaaa!' });
    submit();

    // Two classes (lower + symbol) is enough; the backend NIST policy mirrors
    // this, so rejecting it here would block a legal password.
    expect(
      screen.queryByText('Password must mix at least two of: letters, numbers, symbols'),
    ).toBeNull();
    await waitFor(() => expect(register).toHaveBeenCalled());
  });

  it('requires the confirmation to match', () => {
    render(<SignupPage />);
    fill({ password: 'Passw0rd!abc' });
    fireEvent.change(screen.getByLabelText('Confirm password'), {
      target: { value: 'Passw0rd!xyz' },
    });
    submit();

    expect(screen.getByText('Passwords do not match')).toBeInTheDocument();
    expect(register).not.toHaveBeenCalled();
  });

  it('creates the account and routes to login on success', async () => {
    render(<SignupPage />);
    fill();
    submit();

    await waitFor(() =>
      expect(register).toHaveBeenCalledWith(
        'ada@wildframe.test',
        'Passw0rd!abc',
        'Ada',
        'Lovelace',
      ),
    );
    // Registration is not a login: the backend has not issued a session yet.
    await waitFor(() => expect(push).toHaveBeenCalledWith('/login'));
  });

  it('explains a duplicate-email conflict and stays on the form', async () => {
    register.mockRejectedValue({ response: { status: 409 } });
    render(<SignupPage />);

    fill();
    submit();

    await waitFor(() =>
      expect(
        screen.getByText('An account with this email already exists. Try signing in.'),
      ).toBeInTheDocument(),
    );
    expect(push).not.toHaveBeenCalled();
  });

  it('translates a rate-limited response into retry guidance', async () => {
    register.mockRejectedValue({ response: { status: 429 } });
    render(<SignupPage />);

    fill();
    submit();

    await waitFor(() =>
      expect(
        screen.getByText('Too many attempts. Please wait a minute and try again.'),
      ).toBeInTheDocument(),
    );
    expect(push).not.toHaveBeenCalled();
  });

  it('falls back to generic copy for a non-HTTP failure', async () => {
    register.mockRejectedValue(new Error('socket hang up'));
    render(<SignupPage />);

    fill();
    submit();

    await waitFor(() =>
      expect(
        screen.getByText('Could not create the account. Please try again.'),
      ).toBeInTheDocument(),
    );
  });

  it('disables the submit button and ignores clicks while registration is in flight', () => {
    useAuthStore.setState({ isLoading: true });
    render(<SignupPage />);

    fill();
    const button = screen.getByRole('button', { name: 'Creating account...' });
    expect(button).toBeDisabled();

    fireEvent.click(button);

    expect(register).not.toHaveBeenCalled();
  });

  it('returns an existing visitor to the sign-in page', () => {
    render(<SignupPage />);

    expect(screen.getByText(/already have an account/i)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Sign in' })).toHaveAttribute('href', '/login');
  });
});
