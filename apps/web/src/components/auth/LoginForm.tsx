'use client';

import { useState } from 'react';
import { useAuth } from '@/hooks';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { toast } from 'sonner';
import { getApiErrorMessage } from '@/api/client';

export function LoginForm() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [mfaCode, setMfaCode] = useState('');
  const [mfaStep, setMfaStep] = useState(false);
  const [errors, setErrors] = useState<{ email?: string; password?: string; mfa?: string }>({});
  const { login, verifyMfa, isLoading } = useAuth();
  const router = useRouter();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErrors({});

    if (!mfaStep) {
      if (!email) {
        setErrors({ email: 'Email is required' });
        return;
      }
      if (!password) {
        setErrors({ password: 'Password is required' });
        return;
      }
      try {
        const result = await login(email, password);
        if (result === 'mfa') {
          setMfaStep(true);
          toast.info('Two-step verification required');
          return;
        }
        toast.success('Welcome back!');
        router.push('/browse');
      } catch (error) {
        const msg = getApiErrorMessage(error, 'Invalid email or password');
        setErrors({ password: msg });
        toast.error(msg);
      }
    } else {
      try {
        await verifyMfa(mfaCode);
        toast.success('Welcome back!');
        router.push('/browse');
      } catch (error) {
        const msg = getApiErrorMessage(error, 'Invalid verification code', 'Invalid verification code');
        setErrors({ mfa: msg });
        toast.error(msg);
      }
    }
  };

  return (
    <div className="min-h-screen bg-dark-950 flex items-center justify-center px-4">
      <div className="w-full max-w-md bg-dark-900/80 backdrop-blur-xl border border-dark-700/50 p-8 rounded-xl shadow-2xl">
        <h1 className="text-3xl font-bold text-white mb-8">Sign In</h1>

        <form onSubmit={handleSubmit} className="space-y-5" noValidate>
          {errors.password && (
            <div
              role="alert"
              className="bg-red-500/10 border border-red-500/20 text-red-400 p-3 rounded-lg text-sm"
            >
              {errors.password}
            </div>
          )}
          {errors.mfa && (
            <div
              role="alert"
              className="bg-red-500/10 border border-red-500/20 text-red-400 p-3 rounded-lg text-sm"
            >
              {errors.mfa}
            </div>
          )}

          {mfaStep ? (
            <div>
              <label htmlFor="mfa-code" className="block text-sm font-medium text-gray-300 mb-2">
                Verification code
              </label>
              <input
                id="mfa-code"
                type="text"
                inputMode="numeric"
                autoFocus
                value={mfaCode}
                onChange={(e) => setMfaCode(e.target.value)}
                className="w-full bg-dark-800 text-white px-4 py-3 rounded-lg border border-dark-600 focus:border-red-600 focus:outline-none focus:ring-2 focus:ring-red-600/50 placeholder-gray-500 tracking-[0.4em]"
                placeholder="6-digit code"
                required
              />
            </div>
          ) : (
            <>
              <div>
                <label htmlFor="email" className="block text-sm font-medium text-gray-300 mb-2">
                  Email
                </label>
                <input
                  id="email"
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className="w-full bg-dark-800 text-white px-4 py-3 rounded-lg border border-dark-600 focus:border-red-600 focus:outline-none focus:ring-2 focus:ring-red-600/50 placeholder-gray-500"
                  placeholder="you@example.com"
                  required
                />
                {errors.email && (
                  <p role="alert" className="text-red-400 text-xs mt-1.5">
                    {errors.email}
                  </p>
                )}
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
                  className="w-full bg-dark-800 text-white px-4 py-3 rounded-lg border border-dark-600 focus:border-red-600 focus:outline-none focus:ring-2 focus:ring-red-600/50 placeholder-gray-500"
                  placeholder="Enter your password"
                  required
                />
              </div>
            </>
          )}

          <button
            type="submit"
            disabled={isLoading || (mfaStep && !mfaCode)}
            className="w-full bg-red-600 hover:bg-red-700 text-white py-3 rounded-lg font-semibold transition-colors disabled:opacity-50"
          >
            {isLoading ? 'Working...' : mfaStep ? 'Verify' : 'Sign In'}
          </button>
        </form>

        <p className="text-gray-400 mt-6 text-center text-sm">
          New to Wildframe?{' '}
          <Link href="/signup" className="text-white font-medium hover:text-red-500 transition-colors">
            Sign up now
          </Link>
        </p>
      </div>
    </div>
  );
}