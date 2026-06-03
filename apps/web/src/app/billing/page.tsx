'use client';

import { useQuery, useMutation } from '@tanstack/react-query';
import { apiClient } from '@/api/client';
import { useUser, useAuth } from '@/hooks';
import { useRouter } from 'next/navigation';
import { useEffect, useState } from 'react';

const TIERS = [
  { id: 'free', name: 'Free', price: '$0', features: ['Ads supported', '480p quality', '1 device'] },
  { id: 'basic', name: 'Basic', price: '$9.99', features: ['Ad-free', '1080p quality', '2 devices'] },
  { id: 'premium', name: 'Premium', price: '$15.99', features: ['Ad-free', '4K quality', '4 devices', 'Download offline'] },
  { id: 'family', name: 'Family', price: '$22.99', features: ['Ad-free', '4K quality', '6 devices', 'Download offline', 'Parental controls'] },
];

export default function BillingPage() {
  const user = useUser();
  const { isAuthenticated } = useAuth();
  const router = useRouter();
  const [currentTier, setCurrentTier] = useState<string | null>(null);

  useEffect(() => {
    if (!isAuthenticated) {
      router.push('/login');
    }
  }, [isAuthenticated, router]);

  const { data: subscriptionData } = useQuery({
    queryKey: ['subscription', user?.id],
    queryFn: () => user ? apiClient.getSubscription(user.id) : Promise.reject(),
    enabled: !!user,
  });

  const upgradeMutation = useMutation({
    mutationFn: (tier: string) => user ? apiClient.upgradeSubscription(user.id, tier) : Promise.reject(),
    onSuccess: () => {
      alert('Subscription upgraded successfully!');
    },
    onError: () => {
      alert('Failed to upgrade subscription');
    },
  });

  return (
    <div className="min-h-screen bg-black">
      <div className="max-w-6xl mx-auto px-4 py-8">
        <h1 className="text-3xl font-bold mb-4">Choose Your Plan</h1>
        <p className="text-gray-400 mb-12">Current plan: <span className="text-red-600 font-semibold">{subscriptionData?.data?.tier || 'Loading...'}</span></p>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
          {TIERS.map((tier) => (
            <div
              key={tier.id}
              className={`border-2 rounded-lg p-6 transition ${
                subscriptionData?.data?.tier === tier.id
                  ? 'border-red-600 bg-red-600/10'
                  : 'border-gray-700 hover:border-gray-500'
              }`}
            >
              <h3 className="text-2xl font-bold mb-2">{tier.name}</h3>
              <div className="text-3xl font-bold mb-6">{tier.price}<span className="text-lg text-gray-400">/mo</span></div>

              <ul className="space-y-3 mb-6">
                {tier.features.map((feature) => (
                  <li key={feature} className="text-gray-300 flex items-start">
                    <span className="text-red-600 mr-2">✓</span>
                    {feature}
                  </li>
                ))}
              </ul>

              {subscriptionData?.data?.tier === tier.id ? (
                <button className="w-full bg-gray-700 text-white py-3 rounded font-semibold cursor-default">
                  Current Plan
                </button>
              ) : (
                <button
                  onClick={() => upgradeMutation.mutate(tier.id)}
                  disabled={upgradeMutation.isPending}
                  className="w-full bg-red-600 hover:bg-red-700 text-white py-3 rounded font-semibold transition disabled:opacity-50"
                >
                  {upgradeMutation.isPending ? 'Upgrading...' : 'Select Plan'}
                </button>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
