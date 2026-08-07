'use client';

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '@/api/client';
import { HomeShell } from '@/components/layout/HomeShell';
import { useIsAuthenticated, useUser } from '@/hooks';
import { useRouter } from 'next/navigation';
import { useEffect } from 'react';
import { toast } from 'sonner';
import { Subscription } from '@/types';

interface Plan {
  id: 'avod' | 'svod' | 'tvod';
  name: string;
  price: string;
  tagline: string;
  features: string[];
  highlighted?: boolean;
}

const PLANS: Plan[] = [
  {
    id: 'avod',
    name: 'Free',
    price: '$0',
    tagline: 'Watch with ads',
    features: ['Unlimited movies & shows', 'SD quality', '1 stream', 'Watch on 1 device'],
  },
  {
    id: 'svod',
    name: 'Premium',
    price: '$7.99',
    tagline: 'Ad-free, on every screen',
    features: ['Ad-free streaming', 'Full HD quality', '2 streams', 'Download offline', 'Cancel anytime'],
    highlighted: true,
  },
  {
    id: 'tvod',
    name: 'Pay-Per-View',
    price: 'From $3.99',
    tagline: 'Rent the latest releases',
    features: ['Rent or buy', 'Latest blockbusters', 'HD quality', '48h rental window'],
  },
];

export default function BillingPage() {
  const isAuthenticated = useIsAuthenticated();
  const user = useUser();
  const router = useRouter();
  const queryClient = useQueryClient();

  useEffect(() => {
    if (!isAuthenticated) router.push('/login');
  }, [isAuthenticated, router]);

  const { data: subscription } = useQuery({
    queryKey: ['billing-subscription', user?.id],
    queryFn: () => (user ? apiClient.getSubscription(user.id) : Promise.reject(new Error('no user'))),
    enabled: isAuthenticated && !!user,
  });

  const subscribeMutation = useMutation({
    mutationFn: (tier: 'avod' | 'svod' | 'tvod') => apiClient.subscribe(user!.id, tier),
    onSuccess: (_data, tier) => {
      toast.success(
        tier === 'svod' ? 'Welcome to Premium!' : tier === 'tvod' ? 'Pay-per-view enabled' : 'Switched to the free plan'
      );
      queryClient.invalidateQueries({ queryKey: ['billing-subscription'] });
    },
    onError: () => toast.error('Could not update your plan'),
  });

  const cancelMutation = useMutation({
    mutationFn: () => apiClient.cancelSubscription(user!.id),
    onSuccess: () => {
      toast.success('Subscription cancelled — you are back on the free plan');
      queryClient.invalidateQueries({ queryKey: ['billing-subscription'] });
    },
    onError: () => toast.error('Could not cancel the subscription'),
  });

  const current: Subscription | null = subscription ?? null;

  return (
    <HomeShell>
      <div className="pt-24 pb-10 px-4 sm:px-6 lg:px-8">
        <div className="max-w-5xl mx-auto">
          <h1 className="text-3xl font-bold text-white mb-2">Choose your plan</h1>
          <p className="text-gray-400 mb-8">
            {current?.tier
              ? <>You are currently on the <span className="uppercase text-white font-medium">{current.tier}</span> plan.</>
              : 'Pick the plan that fits how you watch.'}
          </p>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-10">
            {PLANS.map((plan) => {
              const isCurrent = current?.tier === plan.id;
              return (
                <div
                  key={plan.id}
                  className={`relative rounded-xl border p-6 flex flex-col ${
                    plan.highlighted
                      ? 'border-red-600 bg-gradient-to-b from-red-600/10 to-dark-900'
                      : 'border-dark-700 bg-dark-900'
                  }`}
                >
                  {plan.highlighted && (
                    <span className="absolute -top-3 left-6 bg-red-600 text-white text-[10px] font-bold uppercase tracking-wider px-3 py-1 rounded-full">
                      Most popular
                    </span>
                  )}
                  <h2 className="text-lg font-bold text-white">{plan.name}</h2>
                  <p className="text-xs text-gray-500 mb-4">{plan.tagline}</p>
                  <p className="text-3xl font-bold text-white mb-6">
                    {plan.price}
                    {plan.id !== 'avod' && plan.id !== 'tvod' && (
                      <span className="text-sm text-gray-400 font-normal">/mo</span>
                    )}
                  </p>
                  <ul className="space-y-2 mb-8 flex-1">
                    {plan.features.map((f) => (
                      <li key={f} className="flex items-start gap-2 text-sm text-gray-300">
                        <svg className="w-4 h-4 text-red-500 mt-0.5 flex-shrink-0" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                          <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
                        </svg>
                        {f}
                      </li>
                    ))}
                  </ul>
                  <button
                    onClick={() => subscribeMutation.mutate(plan.id)}
                    disabled={isCurrent || subscribeMutation.isPending}
                    className={`w-full py-2.5 rounded-lg text-sm font-semibold transition-colors ${
                      isCurrent
                        ? 'bg-dark-800 text-gray-500 cursor-default'
                        : plan.highlighted
                          ? 'bg-red-600 hover:bg-red-700 text-white'
                          : 'bg-dark-800 hover:bg-dark-700 text-white'
                    }`}
                  >
                    {isCurrent ? 'Current plan' : subscribeMutation.isPending ? 'Updating...' : plan.id === 'avod' ? 'Switch to Free' : 'Choose'}
                  </button>
                </div>
              );
            })}
          </div>

          {current?.tier && current.tier !== 'avod' && (
            <div className="flex items-center justify-between bg-dark-900 border border-dark-800 rounded-xl p-5">
              <div>
                <p className="text-sm font-medium text-white">Cancel Premium</p>
                <p className="text-xs text-gray-500">
                  Your access stays active until the end of the current period, then you return to the free plan.
                </p>
              </div>
              <button
                onClick={() => cancelMutation.mutate()}
                disabled={cancelMutation.isPending}
                className="text-sm text-red-400 hover:text-red-300 transition-colors disabled:opacity-50 flex-shrink-0 ml-4"
              >
                {cancelMutation.isPending ? 'Cancelling...' : 'Cancel subscription'}
              </button>
            </div>
          )}

          <p className="text-xs text-gray-600 mt-8">
            Payments are processed securely. Free plan is ad-supported. Prices may vary by region.
          </p>
        </div>
      </div>
    </HomeShell>
  );
}