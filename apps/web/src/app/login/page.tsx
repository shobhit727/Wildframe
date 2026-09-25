'use client';

import { useState } from 'react';
import { useAuth } from '@/hooks';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { toast } from 'sonner';
import { REGEX } from '@/constants';
import { getApiErrorMessage } from '@/api/client';

export default function LoginPage() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [mfaCode, setMfaCode] = useState('');
  const [mfaStep, setMfaStep] = useState(false);
  const [errors, setErrors] = useState<{ email?: string; password?: string; mfa?: string }>({});
  const { login, verifyMfa, isLoading } = useAuth();
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
    if (isLoading || (!mfaStep && !validate())) return;
    setErrors({});

    try {
      if (mfaStep) {
        if (!mfaCode.trim()) {
          setErrors({ mfa: 'Enter a verification code' });
          return;
        }
        await verifyMfa(mfaCode.trim());
      } else if (await login(email, password) === 'mfa') {
        setMfaStep(true);
        setPassword('');
        toast.info('Two-step verification required');
        return;
      }
      toast.success('Welcome back!');
      router.push('/browse');
    } catch (error) {
      const msg = mfaStep
        ? getApiErrorMessage(error, 'Invalid verification code', 'Invalid verification code')
        : getApiErrorMessage(error, 'Invalid email or password. Please try again.');
      setErrors(mfaStep ? { mfa: msg } : { password: msg });
      toast.error(msg);
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

          <form onSubmit={handleSubmit} className="space-y-4" noValidate>
            {mfaStep ? (
              <div>
                <label htmlFor="mfa-code" className="text-gray-400 text-sm">
                  Authenticator or backup code
                </label>
                <input
                  id="mfa-code"
                  value={mfaCode}
                  onChange={(e) => setMfaCode(e.target.value)}
                  autoComplete="one-time-code"
                  className={inputClass('mfa')}
                  aria-invalid={!!errors.mfa}
                />
                {errors.mfa && <p role="alert" className="text-[#e87c03] text-[13px] mt-1.5">{errors.mfa}</p>}
                <button type="button" disabled={isLoading} className="text-gray-400 text-sm mt-3" onClick={() => {
                  setMfaStep(false);
                  setMfaCode('');
                  setErrors({});
                }}>Back to sign in</button>
              </div>
            ) : (
              <>
            <div>
              <label htmlFor="email" className="sr-only">
                Email
              </label>
              <input
                id="email"
                type="email"
                value={email}
                onChange={(e) => {
                  setEmail(e.target.value);
                  setErrors((p) => ({ ...p, email: undefined }));
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
              <label htmlFor="password" className="sr-only">
                Password
              </label>
              <input
                id="password"
                type="password"
                value={password}
                onChange={(e) => {
                  setPassword(e.target.value);
                  setErrors((p) => ({ ...p, password: undefined }));
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
              </>
            )}

            <button
              type="submit"
              disabled={isLoading}
              className="w-full bg-[#E50914] hover:bg-[#f40612] text-white py-3.5 rounded font-semibold transition-colors disabled:opacity-50"
            >
              {isLoading ? 'Signing in...' : mfaStep ? 'Verify' : 'Sign In'}
            </button>
          </form>

          <p className="text-gray-400 mt-6 text-center text-sm">
            New to Wildframe?{' '}
            <Link href="/signup" className="text-white font-medium hover:text-[#E50914] transition-colors">
              Sign up now
            </Link>
          </p>
        </div>
      </div>
    </div>
  );
}