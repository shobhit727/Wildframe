import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

import { LoginForm } from '@/components/auth/LoginForm';
import { useAuthStore } from '@/stores/auth';

const login = vi.fn();
const push = vi.fn();

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: push }),
}));

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

beforeEach(() => {
  login.mockReset();
  push.mockReset();
  useAuthStore.setState({ isLoading: false, login });
});

describe('LoginForm', () => {
  it('validates that email is required', () => {
    render(<LoginForm />);
    fireEvent.submit(screen.getByRole("button", { name: /sign in/i }).closest("form")!);

    expect(screen.getByText('Email is required')).toBeInTheDocument();
    expect(login).not.toHaveBeenCalled();
  });

  it('validates that password is required', () => {
    render(<LoginForm />);
    fireEvent.change(screen.getByPlaceholderText('you@example.com'), { target: { value: 'a@b.com' } });
    fireEvent.submit(screen.getByRole("button", { name: /sign in/i }).closest("form")!);

    expect(screen.getByText('Password is required')).toBeInTheDocument();
    expect(login).not.toHaveBeenCalled();
  });

  it('calls login and navigates to browse on success', async () => {
    login.mockResolvedValue(undefined);
    render(<LoginForm />);

    fireEvent.change(screen.getByPlaceholderText('you@example.com'), { target: { value: 'a@b.com' } });
    fireEvent.change(screen.getByPlaceholderText('Enter your password'), { target: { value: 'secret' } });
    fireEvent.submit(screen.getByRole("button", { name: /sign in/i }).closest("form")!);

    await waitFor(() => expect(login).toHaveBeenCalledWith('a@b.com', 'secret'));
    await waitFor(() => expect(push).toHaveBeenCalledWith('/browse'));
  });

  it('shows an error message when login fails', async () => {
    login.mockRejectedValue(new Error('bad creds'));
    render(<LoginForm />);

    fireEvent.change(screen.getByPlaceholderText('you@example.com'), { target: { value: 'a@b.com' } });
    fireEvent.change(screen.getByPlaceholderText('Enter your password'), { target: { value: 'wrong' } });
    fireEvent.submit(screen.getByRole("button", { name: /sign in/i }).closest("form")!);

    await waitFor(() =>
      expect(screen.getByText('Invalid email or password')).toBeInTheDocument(),
    );
  });
});