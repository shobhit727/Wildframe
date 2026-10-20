import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import LoginPage from '@/app/login/page';
import { useAuthStore } from '@/stores/auth';

const push = vi.fn();

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push, replace: vi.fn(), refresh: vi.fn() }),
}));

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

const login = vi.fn();
const verifyMfa = vi.fn();

function submit() {
  fireEvent.click(screen.getByRole('button', { name: /^(sign in|verify)$/i }));
}

function fillCredentials(email = 'ada@wildframe.test', password = 'secret1') {
  fireEvent.change(screen.getByLabelText('Email'), { target: { value: email } });
  fireEvent.change(screen.getByLabelText('Password'), { target: { value: password } });
}

beforeEach(() => {
  push.mockReset();
  login.mockReset().mockResolvedValue('ok');
  verifyMfa.mockReset().mockResolvedValue(undefined);
  useAuthStore.setState({
    user: null,
    token: null,
    isAuthenticated: false,
    mfaChallenge: null,
    isLoading: false,
    login,
    verifyMfa,
  });
});

describe('LoginPage (/login)', () => {
  it('requires an email address', () => {
    render(<LoginPage />);
    submit();

    // An empty submit legitimately reports every empty required field at once.
    expect(screen.getByText('Email is required')).toBeInTheDocument();
    expect(screen.getByText('Password is required')).toBeInTheDocument();
    expect(login).not.toHaveBeenCalled();
  });

  it('rejects a malformed email address', () => {
    render(<LoginPage />);
    fillCredentials('not-an-email', 'secret1');
    submit();

    expect(screen.getByRole('alert')).toHaveTextContent('Enter a valid email address');
    expect(login).not.toHaveBeenCalled();
  });

  it('requires a password', () => {
    render(<LoginPage />);
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'ada@wildframe.test' } });
    submit();

    expect(screen.getByRole('alert')).toHaveTextContent('Password is required');
    expect(login).not.toHaveBeenCalled();
  });

  it('enforces a six character password minimum', () => {
    render(<LoginPage />);
    fillCredentials('ada@wildframe.test', 'abc');
    submit();

    expect(screen.getByRole('alert')).toHaveTextContent('Password must be at least 6 characters');
    expect(login).not.toHaveBeenCalled();
  });

  it('surfaces the invalid-credentials message and stays put on a rejected login', async () => {
    login.mockRejectedValue({ response: { status: 401 } });
    render(<LoginPage />);

    fillCredentials();
    submit();

    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent('Invalid email or password'),
    );
    expect(push).not.toHaveBeenCalled();
  });

  it('translates a rate-limited response into retry guidance', async () => {
    login.mockRejectedValue({ response: { status: 429 } });
    render(<LoginPage />);

    fillCredentials();
    submit();

    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent(
        'Too many attempts. Please wait a minute and try again.',
      ),
    );
    expect(push).not.toHaveBeenCalled();
  });

  it('signs the user in and routes to browse on success', async () => {
    render(<LoginPage />);

    fillCredentials();
    submit();

    await waitFor(() =>
      expect(login).toHaveBeenCalledWith('ada@wildframe.test', 'secret1'),
    );
    await waitFor(() => expect(push).toHaveBeenCalledWith('/browse'));
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('clears the password error as soon as the password is edited', async () => {
    login.mockRejectedValue({ response: { status: 401 } });
    render(<LoginPage />);

    fillCredentials();
    submit();
    await waitFor(() =>
      expect(screen.getByText('Invalid email or password. Please try again.')).toBeInTheDocument(),
    );

    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'a-new-secret' } });

    // Stale error copy under a field the user has already corrected reads as
    // "still broken" and discourages the retry.
    expect(screen.queryByText('Invalid email or password. Please try again.')).toBeNull();
  });

  it('switches to the MFA step and asks for a code when login demands one', async () => {
    login.mockResolvedValue('mfa');
    render(<LoginPage />);

    fillCredentials();
    submit();

    const codeField = await screen.findByLabelText('Authenticator or backup code');
    expect(codeField).toBeInTheDocument();
    // The password must be dropped so the code is the only credential in view.
    expect(screen.queryByLabelText('Password')).toBeNull();
    expect(screen.getByRole('button', { name: 'Verify' })).toBeInTheDocument();
    expect(push).not.toHaveBeenCalled();
  });

  it('validates the MFA code is non-empty before verifying', async () => {
    login.mockResolvedValue('mfa');
    render(<LoginPage />);

    fillCredentials();
    submit();
    await screen.findByLabelText('Authenticator or backup code');
    submit();

    expect(screen.getByRole('alert')).toHaveTextContent('Enter a verification code');
    expect(verifyMfa).not.toHaveBeenCalled();
  });

  it('verifies the trimmed code and routes to browse', async () => {
    login.mockResolvedValue('mfa');
    render(<LoginPage />);

    fillCredentials();
    submit();
    const codeField = await screen.findByLabelText('Authenticator or backup code');

    fireEvent.change(codeField, { target: { value: '  123456  ' } });
    submit();

    // Surrounding whitespace from an authenticator app would otherwise be
    // sent verbatim and rejected by the backend.
    await waitFor(() => expect(verifyMfa).toHaveBeenCalledWith('123456'));
    await waitFor(() => expect(push).toHaveBeenCalledWith('/browse'));
  });

  it('reports a bad MFA code against the code field', async () => {
    login.mockResolvedValue('mfa');
    verifyMfa.mockRejectedValue({ response: { status: 401 } });
    render(<LoginPage />);

    fillCredentials();
    submit();
    const codeField = await screen.findByLabelText('Authenticator or backup code');

    fireEvent.change(codeField, { target: { value: '000000' } });
    submit();

    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent('Invalid verification code'),
    );
    expect(push).not.toHaveBeenCalled();
  });

  it('returns to the credential form when backing out of MFA', async () => {
    login.mockResolvedValue('mfa');
    render(<LoginPage />);

    fillCredentials();
    submit();
    await screen.findByLabelText('Authenticator or backup code');

    fireEvent.click(screen.getByRole('button', { name: 'Back to sign in' }));

    expect(screen.getByLabelText('Email')).toBeInTheDocument();
    expect(screen.getByLabelText('Password')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('disables the submit button and ignores clicks while a login is in flight', () => {
    useAuthStore.setState({ isLoading: true });
    render(<LoginPage />);

    fillCredentials();
    const button = screen.getByRole('button', { name: 'Signing in...' });
    expect(button).toBeDisabled();

    fireEvent.click(button);

    // The guard must short-circuit, not queue a second credential submission.
    expect(login).not.toHaveBeenCalled();
  });

  it('links an existing visitor onward to sign up', () => {
    render(<LoginPage />);

    expect(screen.getByText(/new to wildframe/i)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Sign up now' })).toHaveAttribute('href', '/signup');
  });
});
