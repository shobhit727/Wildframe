'use client';

import { useState } from 'react';
import { useAuth } from '@/hooks';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { toast } from 'sonner';
import { getApiErrorMessage } from '@/api/client';

export function SignupForm() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [firstName, setFirstName] = useState('');
  const [lastName, setLastName] = useState('');
  const [errors, setErrors] = useState<Record<string, string>>({});
  const { register, isLoading } = useAuth();
  const router = useRouter();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErrors({});

    try {
      await register(email, password, firstName, lastName);
      toast.success('Welcome to Wildframe!');
      router.push('/browse');
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

  return (
    <div className="min-h-screen bg-dark-950 flex items-center justify-center px-4">
      <div className="w-full max-w-md bg-dark-900/80 backdrop-blur-xl border border-dark-700/50 p-8 rounded-xl shadow-2xl">
        <h1 className="text-3xl font-bold text-white mb-8">Sign Up</h1>

        <form onSubmit={handleSubmit} className="space-y-5" noValidate>
          {errors.email && (
            <div
              role="alert"
              className="bg-red-500/10 border border-red-500/20 text-red-400 p-3 rounded-lg text-sm"
            >
              {errors.email}
            </div>
          )}

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label htmlFor="firstName" className="block text-sm font-medium text-gray-300 mb-2">
                First Name
              </label>
              <input
                id="firstName"
                type="text"
                value={firstName}
                onChange={(e) => setFirstName(e.target.value)}
                className="w-full bg-dark-800 text-white px-4 py-3 rounded-lg border border-dark-600 focus:outline-none focus:ring-2 focus:ring-red-600/50 placeholder-gray-500"
                placeholder="John"
                required
              />
            </div>
            <div>
              <label htmlFor="lastName" className="block text-sm font-medium text-gray-300 mb-2">
                Last Name
              </label>
              <input
                id="lastName"
                type="text"
                value={lastName}
                onChange={(e) => setLastName(e.target.value)}
                className="w-full bg-dark-800 text-white px-4 py-3 rounded-lg border border-dark-600 focus:outline-none focus:ring-2 focus:ring-red-600/50 placeholder-gray-500"
                placeholder="Doe"
                required
              />
            </div>
          </div>

          <div>
            <label htmlFor="email" className="block text-sm font-medium text-gray-300 mb-2">
              Email
            </label>
            <input
              id="email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-full bg-dark-800 text-white px-4 py-3 rounded-lg border border-dark-600 focus:outline-none focus:ring-2 focus:ring-red-600/50 placeholder-gray-500"
              placeholder="you@example.com"
              required
            />
          </div>

          <div>
            <label htmlFor="password" className="block text-sm font-medium text-gray-300 mb-2">
              Password
            </label>
            <input
              id="password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full bg-dark-800 text-white px-4 py-3 rounded-lg border border-dark-600 focus:outline-none focus:ring-2 focus:ring-red-600/50 placeholder-gray-500"
              placeholder="Create a password"
              required
            />
          </div>

          <button
            type="submit"
            disabled={isLoading}
            className="w-full bg-red-600 hover:bg-red-700 text-white py-3 rounded-lg font-semibold transition-colors disabled:opacity-50"
          >
            {isLoading ? 'Creating account...' : 'Sign Up'}
          </button>
        </form>

        <p className="text-gray-400 mt-6 text-center text-sm">
          Already have an account?{' '}
          <Link href="/login" className="text-white font-medium hover:text-red-500 transition-colors">
            Sign in
          </Link>
        </p>
      </div>
    </div>
  );
}