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

  const inputClass = (field: keyof typeof errors) =>
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
          <h1 className="text-3xl font-bold text-white mb-7">Sign In</h1>

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <input
                id="email"
                type="email"
                value={email}
                onChange={(e) => { setEmail(e.target.value); setErrors((p) => ({ ...p, email: undefined })); }}
                className={inputClass('email')}
                placeholder="Email or phone number"
                aria-label="Email"
              />
              {errors.email && <p className="text-[#e87c03] text-[13px] mt-1.5">{errors.email}</p>}
            </div>

            <div>
              <input
                id="password"
                type="password"
                value={password}
                onChange={(e) => { setPassword(e.target.value); setErrors((p) => ({ ...p, password: undefined })); }}
                className={inputClass('password')}
                placeholder="Password"
                aria-label="Password"
              />
              {errors.password && <p className="text-[#e87c03] text-[13px] mt-1.5">{errors.password}</p>}
            </div>

            <button
              type="submit"
              disabled={isLoading}
              className="w-full bg-[#E50914] hover:bg-[#F6121D] text-white py-3 rounded font-semibold transition-colors disabled:opacity-60 disabled:cursor-not-allowed"
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

          <p className="text-[15px] text-gray-400 mt-6">
            New to Wildframe?{' '}
            <Link href="/signup" className="text-white hover:underline">
              Sign up now
            </Link>
          </p>
          <p className="text-[13px] text-gray-500 mt-4 leading-relaxed">
            This page is protected by reCAPTCHA-like measures so we know you&apos;re not a bot.
          </p>
        </div>
      </div>

      {/* Footer bar */}
      <footer className="w-full bg-black/80 border-t border-white/5 py-8 px-8">
        <div className="max-w-md mx-auto">
          <p className="text-[#737373] text-sm mb-4">Questions? Contact us.</p>
          <div className="grid grid-cols-2 gap-2 text-[13px] text-[#737373]">
            {['FAQ', 'Help Center', 'Terms of Use', 'Privacy'].map((l) => (
              <Link key={l} href="#" className="hover:underline">{l}</Link>
            ))}
          </div>
        </div>
      </footer>
    </div>
  );
}