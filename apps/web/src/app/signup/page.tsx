'use client';

import { useState } from 'react';
import { useAuth } from '@/hooks';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { toast } from 'sonner';
import { REGEX } from '@/constants';
import { getApiErrorMessage } from '@/api/client';

export default function SignupPage() {
  const [firstName, setFirstName] = useState('');
  const [lastName, setLastName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [errors, setErrors] = useState<Record<string, string>>({});
  const { register, isLoading } = useAuth();
  const router = useRouter();

  const validate = (): boolean => {
    const newErrors: Record<string, string> = {};
    if (!firstName.trim()) newErrors.firstName = 'First name is required';
    if (!lastName.trim()) newErrors.lastName = 'Last name is required';
    if (!email) {
      newErrors.email = 'Email is required';
    } else if (!REGEX.EMAIL.test(email)) {
      newErrors.email = 'Enter a valid email address';
    }
    if (!password) {
      newErrors.password = 'Password is required';
    } else if (password.length < 12) {
      // Mirrors the backend's NIST 800-63B policy: length is the primary
      // signal; composition classes are not individually required.
      newErrors.password = 'Password must be at least 12 characters';
    } else {
      const classes = [/[a-z]/, /[A-Z]/, /[0-9]/, /[^A-Za-z0-9]/].filter((r) => r.test(password)).length;
      if (classes < 2) {
        newErrors.password = 'Password must mix at least two of: letters, numbers, symbols';
      }
    }
    if (password !== confirmPassword) {
      newErrors.confirmPassword = 'Passwords do not match';
    }
    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!validate()) return;

    try {
      await register(email, password, firstName, lastName);
      toast.success('Account created! Please sign in.');
      router.push('/login');
    } catch (error) {
      const status = (error as { response?: { status?: number } })?.response?.status;
      const msg =
        status === 409
          ? 'An account with this email already exists. Try signing in.'
          : getApiErrorMessage(error, 'Could not create the account. Please try again.');
      setErrors({ email: msg });
      toast.error(msg);
    }
  };

  const inputClass = (field: string) =>
    `w-full bg-[#333] text-white px-4 py-3.5 rounded border-0 transition-colors focus:outline-none focus:bg-[#454545] placeholder-gray-500 ${
      errors[field] ? 'border border-red-500' : ''
    }`;

  return (
    <div className="min-h-screen bg-[#141414] flex flex-col">
      {/* Brand */}
      <header className="px-8 py-6">
        <Link href="/" className="text-3xl font-bold tracking-tight text-[#E50914] select-none inline-block">
          WILDFRAME
        </Link>
      </header>

      <div className="flex-1 flex items-center justify-center px-4 pb-16">
        <div className="w-full max-w-md bg-black/70 border border-white/5 p-14 rounded-lg animate-fade-in">
          <h1 className="text-3xl font-bold text-white mb-7">Create Account</h1>

          <form onSubmit={handleSubmit} className="space-y-4" noValidate>
            {/* Name Row */}
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label htmlFor="firstName" className="sr-only">First name</label>
                <input
                  id="firstName"
                  type="text"
                  value={firstName}
                  onChange={(e) => {
                    setFirstName(e.target.value);
                    setErrors((p) => ({ ...p, firstName: '' }));
                  }}
                  className={inputClass('firstName')}
                  placeholder="First name"
                  aria-label="First name"
                />
                {errors.firstName && (
                  <p role="alert" className="text-[#e87c03] text-[13px] mt-1.5">
                    {errors.firstName}
                  </p>
                )}
              </div>
              <div>
                <label htmlFor="lastName" className="sr-only">Last name</label>
                <input
                  id="lastName"
                  type="text"
                  value={lastName}
                  onChange={(e) => {
                    setLastName(e.target.value);
                    setErrors((p) => ({ ...p, lastName: '' }));
                  }}
                  className={inputClass('lastName')}
                  placeholder="Last name"
                  aria-label="Last name"
                />
                {errors.lastName && (
                  <p role="alert" className="text-[#e87c03] text-[13px] mt-1.5">
                    {errors.lastName}
                  </p>
                )}
              </div>
            </div>

            <div>
              <label htmlFor="email" className="sr-only">Email</label>
              <input
                id="email"
                type="email"
                value={email}
                onChange={(e) => {
                  setEmail(e.target.value);
                  setErrors((p) => ({ ...p, email: '' }));
                }}
                className={inputClass('email')}
                placeholder="Email or phone number"
                aria-label="Email"
              />
              {errors.email && (
                <p role="alert" className="text-[#e87c03] text-[13px] mt-1.5">
                  {errors.email}
                </p>
              )}
            </div>

            <div>
              <label htmlFor="password" className="sr-only">Password</label>
              <input
                id="password"
                type="password"
                value={password}
                onChange={(e) => {
                  setPassword(e.target.value);
                  setErrors((p) => ({ ...p, password: '' }));
                }}
                className={inputClass('password')}
                placeholder="Password"
                aria-label="Password"
              />
              {errors.password && (
                <p role="alert" className="text-[#e87c03] text-[13px] mt-1.5">
                  {errors.password}
                </p>
              )}
            </div>

            <div>
              <label htmlFor="confirmPassword" className="sr-only">Confirm password</label>
              <input
                id="confirmPassword"
                type="password"
                value={confirmPassword}
                onChange={(e) => {
                  setConfirmPassword(e.target.value);
                  setErrors((p) => ({ ...p, confirmPassword: '' }));
                }}
                className={inputClass('confirmPassword')}
                placeholder="Confirm your password"
                aria-label="Confirm password"
              />
              {errors.confirmPassword && (
                <p role="alert" className="text-[#e87c03] text-[13px] mt-1.5">
                  {errors.confirmPassword}
                </p>
              )}
            </div>

            <button
              type="submit"
              disabled={isLoading}
              className="w-full bg-[#E50914] hover:bg-[#F6121D] text-white py-3 rounded font-semibold transition-colors disabled:opacity-60 disabled:cursor-not-allowed"
            >
              {isLoading ? 'Creating account...' : 'Create Account'}
            </button>
          </form>

          <p className="text-gray-400 mt-6 text-center text-sm">
            Already have an account?{' '}
            <Link href="/login" className="text-white font-medium hover:text-[#E50914] transition-colors">
              Sign in
            </Link>
          </p>
        </div>
      </div>
    </div>
  );
}