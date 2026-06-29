'use client';

import { useQuery, useMutation } from '@tanstack/react-query';
import { apiClient } from '@/api/client';
import { useUser } from '@/hooks';
import { HomeShell } from '@/components/layout/HomeShell';
import { Subscription } from '@/types';
import { toast } from 'sonner';
import { clsx } from 'clsx';

const TIERS = [
  {
    id: 'free' as const,
    name: 'Free',
    price: 0,
    displayPrice: '$0',
    features: ['Ad-supported viewing', '480p quality', '1 device', 'Limited catalog'],
    highlight: false,
  },
  {
    id: 'basic' as const,
    name: 'Basic',
    price: 9.99,
    displayPrice: '$9.99',
    features: ['Ad-free viewing', '1080p quality', '2 devices', 'Full catalog'],
    highlight: false,
  },
  {
    id: 'premium' as const,
    name: 'Premium',
    price: 15.99,
    displayPrice: '$15.99',
    features: ['Ad-free viewing', '4K + HDR quality', '4 devices', 'Full catalog', 'Download offline'],
    highlight: true,
  },
  {
    id: 'family' as const,
    name: 'Family',
    price: 22.99,
    displayPrice: '$22.99',
    features: ['Ad-free viewing', '4K + HDR quality', '6 devices', 'Full catalog', 'Download offline', 'Parental controls', 'Kids profiles'],
    highlight: false,
  },
];

export default function BillingPage() {
  const user = useUser();

  const { data: subscriptionData, isLoading: subLoading } = useQuery({
    queryKey: ['subscription', user?.id],
    queryFn: () => user ? apiClient.getSubscription(user.id) : Promise.reject(new Error('No user')),
    enabled: !!user,
  });

  const subscription: Subscription | null = subscriptionData?.data || null;
  const currentTier = subscription?.tier || 'free';

  const upgradeMutation = useMutation({
    mutationFn: (tier: string) => user ? apiClient.upgradeSubscription(user.id, tier) : Promise.reject(new Error('No user')),
    onSuccess: () => {
      toast.success('Plan updated successfully!');
    },
    onError: () => {
      toast.error('Failed to update plan. Please try again.');
    },
  });

  return (
    <HomeShell>
      <div className="pt-24 pb-10 px-4 sm:px-6 lg:px-8 max-w-6xl mx-auto">
        <div className="text-center mb-12">
          <h1 className="text-3xl font-bold text-white mb-3">Choose Your Plan</h1>
          <p className="text-gray-400">
            {subLoading ? 'Loading current plan...' : (
              <>Current plan: <span className="text-red-500 font-semibold capitalize">{currentTier}</span></>
            )}
          </p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-5">
          {TIERS.map((tier) => {
            const isCurrent = currentTier === tier.id;
            const isDowngrade = TIERS.find((t) => t.id === currentTier) && tier.price < (TIERS.find((t) => t.id === currentTier)?.price || 0);

            return (
              <div
                key={tier.id}
                className={clsx(
                  'relative rounded-xl border-2 p-6 transition-all duration-200',
                  isCurrent
                    ? 'border-red-600 bg-red-600/5'
                    : tier.highlight
                      ? 'border-dark-600 bg-dark-900 hover:border-dark-500'
                      : 'border-dark-800 bg-dark-900 hover:border-dark-600'
                )}
              >
                {/* Popular badge */}
                {tier.highlight && !isCurrent && (
                  <div className="absolute -top-3 left-1/2 -translate-x-1/2 bg-red-600 text-white text-xs font-semibold px-3 py-1 rounded-full">
                    Most Popular
                  </div>
                )}

                {/* Plan Name */}
                <h3 className="text-xl font-bold text-white mb-1">{tier.name}</h3>

                {/* Price */}
                <div className="mb-6">
                  <span className="text-3xl font-bold text-white">{tier.displayPrice}</span>
                  <span className="text-gray-400 text-sm">/month</span>
                </div>

                {/* Features */}
                <ul className="space-y-2.5 mb-8">
                  {tier.features.map((feature) => (
                    <li key={feature} className="flex items-start gap-2 text-sm text-gray-300">
                      <svg className="w-4 h-4 text-red-500 flex-shrink-0 mt-0.5" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
                      </svg>
                      {feature}
                    </li>
                  ))}
                </ul>

                {/* CTA Button */}
                {isCurrent ? (
                  <div className="w-full text-center py-2.5 text-sm font-medium text-gray-400 bg-dark-800 rounded-lg">
                    Current Plan
                  </div>
                ) : (
                  <button
                    onClick={() => upgradeMutation.mutate(tier.id)}
                    disabled={upgradeMutation.isPending}
                    className={clsx(
                      'w-full py-2.5 rounded-lg text-sm font-semibold transition-colors disabled:opacity-50',
                      tier.highlight && !isCurrent
                        ? 'bg-red-600 hover:bg-red-700 text-white'
                        : isDowngrade
                          ? 'bg-dark-800 hover:bg-dark-700 text-white border border-dark-600'
                          : 'bg-white hover:bg-gray-100 text-black'
                    )}
                  >
                    {upgradeMutation.isPending ? 'Updating...' : isDowngrade ? 'Downgrade' : 'Upgrade'}
                  </button>
                )}
              </div>
            );
          })}
        </div>

        {/* FAQ */}
        <div className="mt-16 max-w-2xl mx-auto">
          <h2 className="text-xl font-bold text-white text-center mb-8">Frequently Asked Questions</h2>
          <div className="space-y-3">
            {[
              { q: 'Can I change my plan at any time?', a: 'Yes, you can upgrade or downgrade your plan at any time. Changes take effect immediately.' },
              { q: 'How does the free trial work?', a: 'New members get a 7-day free trial of Premium. Cancel anytime before the trial ends.' },
              { q: 'What payment methods are accepted?', a: 'We accept Visa, Mastercard, American Express, and PayPal.' },
            ].map((faq) => (
              <details key={faq.q} className="bg-dark-900 rounded-lg border border-dark-800 overflow-hidden group">
                <summary className="px-5 py-4 text-sm font-medium text-white cursor-pointer list-none flex items-center justify-between hover:bg-dark-800 transition-colors">
                  {faq.q}
                  <svg className="w-4 h-4 text-gray-400 transition-transform group-open:rotate-180" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
                  </svg>
                </summary>
                <div className="px-5 pb-4 text-sm text-gray-400 border-t border-dark-800 pt-3">
                  {faq.a}
                </div>
              </details>
            ))}
          </div>
        </div>
      </div>
    </HomeShell>
  );
}
