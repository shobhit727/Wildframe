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
      newErrors.password = 'Include uppercase, lowercase, number & special character';
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
    `w-full bg-dark-800 text-white px-4 py-3 rounded-lg border transition-colors focus:outline-none focus:ring-2 focus:ring-red-600/50 placeholder-gray-500 ${
      errors[field] ? 'border-red-500' : 'border-dark-600 hover:border-dark-500'
    }`;

  return (
    <div className="min-h-screen bg-dark-950 flex items-center justify-center px-4 relative">
      {/* Background */}
      <div className="absolute inset-0 bg-gradient-to-br from-dark-950 via-dark-900 to-dark-950" />
      <div className="absolute top-1/3 left-1/2 -translate-x-1/2 w-[600px] h-[400px] bg-red-600/5 blur-3xl rounded-full" />

      <div className="relative w-full max-w-md">
        {/* Brand */}
        <Link href="/" className="block text-center mb-10">
          <span className="text-3xl font-bold tracking-wider text-red-600">WILDFRAME</span>
        </Link>

        {/* Card */}
        <div className="bg-dark-900/80 backdrop-blur-xl border border-dark-700/50 p-8 rounded-xl shadow-2xl shadow-black/40 animate-fade-in">
          <h1 className="text-2xl font-bold text-white mb-2">Create Account</h1>
          <p className="text-sm text-gray-400 mb-8">Start streaming today</p>

          <form onSubmit={handleSubmit} className="space-y-5">
            {/* Name Row */}
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium text-gray-300 mb-2">First Name</label>
                <input
                  type="text"
                  value={firstName}
                  onChange={(e) => { setFirstName(e.target.value); setErrors((p) => ({ ...p, firstName: '' })); }}
                  className={inputClass('firstName')}
                  placeholder="John"
                />
                {errors.firstName && <p className="text-red-400 text-xs mt-1.5">{errors.firstName}</p>}
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-300 mb-2">Last Name</label>
                <input
                  type="text"
                  value={lastName}
                  onChange={(e) => { setLastName(e.target.value); setErrors((p) => ({ ...p, lastName: '' })); }}
                  className={inputClass('lastName')}
                  placeholder="Doe"
                />
                {errors.lastName && <p className="text-red-400 text-xs mt-1.5">{errors.lastName}</p>}
              </div>
            </div>

            {/* Email */}
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-2">Email</label>
              <input
                type="email"
                value={email}
                onChange={(e) => { setEmail(e.target.value); setErrors((p) => ({ ...p, email: '' })); }}
                className={inputClass('email')}
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
                onChange={(e) => { setPassword(e.target.value); setErrors((p) => ({ ...p, password: '' })); }}
                className={inputClass('password')}
                placeholder="Create a password"
              />
              {errors.password && <p className="text-red-400 text-xs mt-1.5">{errors.password}</p>}
            </div>

            {/* Confirm Password */}
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-2">Confirm Password</label>
              <input
                type="password"
                value={confirmPassword}
                onChange={(e) => { setConfirmPassword(e.target.value); setErrors((p) => ({ ...p, confirmPassword: '' })); }}
                className={inputClass('confirmPassword')}
                placeholder="Confirm your password"
              />
              {errors.confirmPassword && <p className="text-red-400 text-xs mt-1.5">{errors.confirmPassword}</p>}
            </div>

            {/* Terms */}
            <label className="flex items-start gap-3 text-sm text-gray-400 cursor-pointer">
              <input type="checkbox" required className="w-4 h-4 mt-0.5 rounded border-dark-600 bg-dark-800 text-red-600 focus:ring-red-600/50" />
              <span>I agree to the <a href="#" className="text-white hover:underline">Terms of Service</a> and <a href="#" className="text-white hover:underline">Privacy Policy</a></span>
            </label>

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
                  Creating account...
                </span>
              ) : 'Create Account'}
            </button>
          </form>

          {/* Sign in link */}
          <p className="text-center text-gray-400 text-sm mt-6">
            Already have an account?{' '}
            <Link href="/login" className="text-white font-medium hover:text-red-500 transition-colors">
              Sign in
            </Link>
          </p>
        </div>
      </div>
    </div>
  );
}
