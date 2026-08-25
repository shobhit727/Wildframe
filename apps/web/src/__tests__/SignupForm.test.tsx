import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

import { SignupForm } from '@/components/auth/SignupForm';
import { useAuthStore } from '@/stores/auth';

const register = vi.fn();
const push = vi.fn();

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: push }),
}));

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

beforeEach(() => {
  register.mockReset();
  push.mockReset();
  useAuthStore.setState({ isLoading: false, register });
});

describe('SignupForm', () => {
  it('submits account details and navigates to browse on success', async () => {
    register.mockResolvedValue(undefined);
    render(<SignupForm />);

    fireEvent.change(screen.getByPlaceholderText('John'), { target: { value: 'Ada' } });
    fireEvent.change(screen.getByPlaceholderText('Doe'), { target: { value: 'Lovelace' } });
    fireEvent.change(screen.getByPlaceholderText('you@example.com'), { target: { value: 'ada@x.io' } });
    fireEvent.change(screen.getByPlaceholderText('Create a password'), { target: { value: 'secret' } });
    fireEvent.submit(screen.getByRole("button", { name: "Sign Up" }).closest("form")!);

    await waitFor(() => expect(register).toHaveBeenCalledWith('ada@x.io', 'secret', 'Ada', 'Lovelace'));
    await waitFor(() => expect(push).toHaveBeenCalledWith('/browse'));
  });

  it('shows an account creation error when registration fails', async () => {
    register.mockRejectedValue(new Error('duplicate'));
    render(<SignupForm />);

    fireEvent.change(screen.getByPlaceholderText('you@example.com'), { target: { value: 'ada@x.io' } });
    fireEvent.change(screen.getByPlaceholderText('Create a password'), { target: { value: 'secret' } });
    fireEvent.submit(screen.getByRole("button", { name: "Sign Up" }).closest("form")!);

    await waitFor(() =>
      expect(screen.getByText(/could not create the account|already exists/i)).toBeInTheDocument(),
    );
    expect(push).not.toHaveBeenCalled();
  });

  it('disables the submit button while loading', () => {
    useAuthStore.setState({ isLoading: true });
    render(<SignupForm />);

    expect(screen.getByRole('button', { name: 'Creating account...' })).toBeDisabled();
  });
});