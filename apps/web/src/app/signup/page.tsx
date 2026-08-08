'use client';

import { useState } from 'react';
import { useAuth } from '@/hooks';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { toast } from 'sonner';
import { REGEX } from '@/constants';

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
    } else if (password.length < 8) {
      newErrors.password = 'Password must be at least 8 characters';
    } else if (!REGEX.PASSWORD.test(password)) {
      newErrors.password = 'Password must include uppercase, lowercase, a number and a special character';
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
    } catch {
      setErrors({ email: 'An account with this email may already exist.' });
      toast.error('Sign up failed');
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

          <form onSubmit={handleSubmit} className="space-y-4">
            {/* Name Row */}
            <div className="grid grid-cols-2 gap-4">
              <div>
                <input
                  type="text"
                  value={firstName}
                  onChange={(e) => { setFirstName(e.target.value); setErrors((p) => ({ ...p, firstName: '' })); }}
                  className={inputClass('firstName')}
                  placeholder="First name"
                  aria-label="First name"
                />
                {errors.firstName && <p className="text-[#e87c03] text-[13px] mt-1.5">{errors.firstName}</p>}
              </div>
              <div>
                <input
                  type="text"
                  value={lastName}
                  onChange={(e) => { setLastName(e.target.value); setErrors((p) => ({ ...p, lastName: '' })); }}
                  className={inputClass('lastName')}
                  placeholder="Last name"
                  aria-label="Last name"
                />
                {errors.lastName && <p className="text-[#e87c03] text-[13px] mt-1.5">{errors.lastName}</p>}
              </div>
            </div>

            <div>
              <input
                type="email"
                value={email}
                onChange={(e) => { setEmail(e.target.value); setErrors((p) => ({ ...p, email: '' })); }}
                className={inputClass('email')}
                placeholder="Email or phone number"
                aria-label="Email"
              />
              {errors.email && <p className="text-[#e87c03] text-[13px] mt-1.5">{errors.email}</p>}
            </div>

            <div>
              <input
                type="password"
                value={password}
                onChange={(e) => { setPassword(e.target.value); setErrors((p) => ({ ...p, password: '' })); }}
                className={inputClass('password')}
                placeholder="Password"
                aria-label="Password"
              />
              {errors.password && <p className="text-[#e87c03] text-[13px] mt-1.5">{errors.password}</p>}
            </div>

            <div>
              <input
                type="password"
                value={confirmPassword}
                onChange={(e) => { setConfirmPassword(e.target.value); setErrors((p) => ({ ...p, confirmPassword: '' })); }}
                className={inputClass('confirmPassword')}
                placeholder="Confirm your password"
                aria-label="Confirm password"
              />
              {errors.confirmPassword && <p className="text-[#e87c03] text-[13px] mt-1.5">{errors.confirmPassword}</p>}
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
                  Creating account...
                </span>
              ) : 'Create Account'}
            </button>
          </form>

          <p className="text-[15px] text-gray-400 mt-6">
            Already have an account?{' '}
            <Link href="/login" className="text-white hover:underline">
              Sign in
            </Link>
          </p>
          <p className="text-[13px] text-gray-500 mt-4 leading-relaxed">
            By creating an account you agree to our Terms of Service and Privacy Policy.
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