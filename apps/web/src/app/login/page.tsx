'use client';

import { useState } from 'react';
import { useAuth } from '@/hooks';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { toast } from 'sonner';
import { REGEX } from '@/constants';

export default function LoginPage() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [errors, setErrors] = useState<{ email?: string; password?: string }>({});
  const { login, isLoading } = useAuth();
  const router = useRouter();

  const validate = (): boolean => {
    const newErrors: { email?: string; password?: string } = {};
    if (!email) {
      newErrors.email = 'Email is required';
    } else if (!REGEX.EMAIL.test(email)) {
      newErrors.email = 'Enter a valid email address';
    }
    if (!password) {
      newErrors.password = 'Password is required';
    } else if (password.length < 6) {
      newErrors.password = 'Password must be at least 6 characters';
    }
    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!validate()) return;

    try {
      await login(email, password);
      toast.success('Welcome back!');
      router.push('/browse');
    } catch {
      setErrors({ password: 'Invalid email or password. Please try again.' });
      toast.error('Sign in failed');
    }
  };

  return (
    <div className="min-h-screen bg-dark-950 flex items-center justify-center px-4 relative">
      {/* Background gradient */}
      <div className="absolute inset-0 bg-gradient-to-br from-dark-950 via-dark-900 to-dark-950" />
      <div className="absolute top-1/4 left-1/2 -translate-x-1/2 w-[600px] h-[600px] bg-red-600/5 blur-3xl rounded-full" />

      <div className="relative w-full max-w-md">
        {/* Brand */}
        <Link href="/" className="block text-center mb-10">
          <span className="text-3xl font-bold tracking-wider text-red-600">WILDFRAME</span>
        </Link>

        {/* Card */}
        <div className="bg-dark-900/80 backdrop-blur-xl border border-dark-700/50 p-8 rounded-xl shadow-2xl shadow-black/40 animate-fade-in">
          <h1 className="text-2xl font-bold text-white mb-2">Sign In</h1>
          <p className="text-sm text-gray-400 mb-8">Welcome back to Wildframe</p>

          <form onSubmit={handleSubmit} className="space-y-5">
            {/* Email */}
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-2">Email</label>
              <input
                type="email"
                value={email}
                onChange={(e) => { setEmail(e.target.value); setErrors((p) => ({ ...p, email: undefined })); }}
                className={`w-full bg-dark-800 text-white px-4 py-3 rounded-lg border transition-colors focus:outline-none focus:ring-2 focus:ring-red-600/50 placeholder-gray-500 ${
                  errors.email ? 'border-red-500' : 'border-dark-600 hover:border-dark-500'
                }`}
                placeholder="you@example.com"
              />
              {errors.email && <p className="text-red-400 text-xs mt-1.5">{errors.email}</p>}
            </div>

            {/* Password */}
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-2">Password</label>
              <input
                type="password"
                value={password}
                onChange={(e) => { setPassword(e.target.value); setErrors((p) => ({ ...p, password: undefined })); }}
                className={`w-full bg-dark-800 text-white px-4 py-3 rounded-lg border transition-colors focus:outline-none focus:ring-2 focus:ring-red-600/50 placeholder-gray-500 ${
                  errors.password ? 'border-red-500' : 'border-dark-600 hover:border-dark-500'
                }`}
                placeholder="Enter your password"
              />
              {errors.password && <p className="text-red-400 text-xs mt-1.5">{errors.password}</p>}
            </div>

            {/* Remember + Forgot */}
            <div className="flex items-center justify-between text-sm">
              <label className="flex items-center gap-2 text-gray-400 cursor-pointer">
                <input type="checkbox" className="w-4 h-4 rounded border-dark-600 bg-dark-800 text-red-600 focus:ring-red-600/50" />
                Remember me
              </label>
              <a href="#" className="text-gray-400 hover:text-gray-200 transition-colors">Forgot password?</a>
            </div>

            {/* Submit */}
            <button
              type="submit"
              disabled={isLoading}
              className="w-full bg-red-600 hover:bg-red-700 text-white py-3 rounded-lg font-semibold transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {isLoading ? (
                <span className="flex items-center justify-center gap-2">
                  <svg className="w-4 h-4 animate-spin" viewBox="0 0 24 24" fill="none">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                  </svg>
                  Signing in...
                </span>
              ) : 'Sign In'}
            </button>
          </form>

          {/* Divider */}
          <div className="flex items-center gap-3 my-6">
            <div className="flex-1 h-px bg-dark-700" />
            <span className="text-xs text-gray-500">OR</span>
            <div className="flex-1 h-px bg-dark-700" />
          </div>

          {/* Social Login Placeholder */}
          <button className="w-full bg-dark-800 hover:bg-dark-700 text-white py-3 rounded-lg font-medium border border-dark-600 transition-colors flex items-center justify-center gap-3">
            <svg className="w-5 h-5" viewBox="0 0 24 24" fill="currentColor"><path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 01-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z" /><path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" /><path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" /><path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" /></svg>
            Continue with Google
          </button>

          {/* Sign up link */}
          <p className="text-center text-gray-400 text-sm mt-6">
            New to Wildframe?{' '}
            <Link href="/signup" className="text-white font-medium hover:text-red-500 transition-colors">
              Sign up now
            </Link>
          </p>
        </div>
      </div>
    </div>
  );
}
