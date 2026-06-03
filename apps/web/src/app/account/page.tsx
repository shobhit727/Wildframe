'use client';

import { useUser, useAuth } from '@/hooks';
import { useRouter } from 'next/navigation';
import { useEffect } from 'react';
import Link from 'next/link';

export default function AccountPage() {
  const user = useUser();
  const { isAuthenticated } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!isAuthenticated) {
      router.push('/login');
    }
  }, [isAuthenticated, router]);

  return (
    <div className="min-h-screen bg-black">
      <div className="max-w-4xl mx-auto px-4 py-8">
        <h1 className="text-3xl font-bold mb-8">Account Settings</h1>

        <div className="bg-gray-900 rounded-lg p-8 mb-8">
          <h2 className="text-xl font-bold mb-4">Profile Information</h2>
          <div className="space-y-4">
            <div>
              <label className="block text-gray-400">Email</label>
              <p className="text-white">{user?.email}</p>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-gray-400">First Name</label>
                <p className="text-white">{user?.firstName}</p>
              </div>
              <div>
                <label className="block text-gray-400">Last Name</label>
                <p className="text-white">{user?.lastName}</p>
              </div>
            </div>
          </div>
        </div>

        <div className="bg-gray-900 rounded-lg p-8">
          <h2 className="text-xl font-bold mb-4">Subscription</h2>
          <Link
            href="/billing"
            className="text-red-600 hover:underline"
          >
            Manage your subscription →
          </Link>
        </div>
      </div>
    </div>
  );
}
