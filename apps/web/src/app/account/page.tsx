'use client';

import { useEffect, useState } from 'react';
import { useUser } from '@/hooks';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '@/api/client';
import { HomeShell } from '@/components/layout/HomeShell';
import { ProfileSkeleton } from '@/components/common/Skeleton';
import { toast } from 'sonner';
import Link from 'next/link';
import * as Tabs from '@radix-ui/react-tabs';

function PencilIcon() {
  return (
    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" d="M16.862 4.487l1.687-1.688a1.875 1.875 0 112.652 2.652L10.582 16.07a4.5 4.5 0 01-1.897 1.13L6 18l.8-2.8a4.5 4.5 0 011.13-1.897l8.932-8.916z" />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg className="w-4 h-4 text-green-500" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
    </svg>
  );
}

const PLAN_FEATURES: Record<string, string[]> = {
  avod: ['Free to watch', 'Ad-supported', 'SD quality', '1 concurrent stream', 'Watch on 1 device'],
  svod: ['Ad-free streaming', 'Up to Full HD', 'Watch on 2 devices', 'Download offline', 'Cancel anytime'],
  tvod: ['Pay-per-view', 'Rent or buy', 'Latest releases', 'HD / HD+ quality'],
};

export default function AccountPage() {
  const user = useUser();
  const queryClient = useQueryClient();
  const [editingProfile, setEditingProfile] = useState(false);
  const [formData, setFormData] = useState({
    firstName: '',
    lastName: '',
    email: '',
    bio: '',
    phone_number: '',
    country: '',
  });

  const userId = user?.id;

  const { data: profile } = useQuery({
    queryKey: ['profile', userId],
    queryFn: () => (userId ? apiClient.getProfile(userId) : Promise.reject(new Error('no user'))),
    enabled: !!userId,
  });

  useEffect(() => {
    if (user)
      setFormData((p) => ({
        firstName: user.firstName,
        lastName: user.lastName,
        email: user.email,
        bio: p.bio || profile?.bio || '',
        phone_number: p.phone_number || profile?.phone_number || '',
        country: p.country || profile?.country || '',
      }));
  }, [user, profile]);

  const { data: subscription, isLoading: subLoading } = useQuery({
    queryKey: ['billing-subscription', userId],
    queryFn: () => (userId ? apiClient.getSubscription(userId) : Promise.reject(new Error('no user'))),
    enabled: !!userId,
  });

  const { data: devices } = useQuery({
    queryKey: ['devices', userId],
    queryFn: () => (userId ? apiClient.getDevices(userId) : Promise.reject(new Error('no user'))),
    enabled: !!userId,
  });

  const { data: preferences } = useQuery({
    queryKey: ['preferences', userId],
    queryFn: () => (userId ? apiClient.getPreferences(userId) : Promise.reject(new Error('no user'))),
    enabled: !!userId,
  });

  const updateProfileMutation = useMutation({
    mutationFn: (data: Record<string, unknown>) => apiClient.updateProfile(userId!, data),
    onSuccess: () => {
      toast.success('Profile updated');
      setEditingProfile(false);
      queryClient.invalidateQueries({ queryKey: ['profile'] });
    },
    onError: () => toast.error('Failed to update profile'),
  });

  const handleSaveProfile = () => {
    updateProfileMutation.mutate({
      bio: formData.bio ?? undefined,
      phone_number: formData.phone_number ?? undefined,
      country: formData.country ?? undefined,
    });
  };

  const updatePrefsMutation = useMutation({
    mutationFn: (data: Record<string, unknown>) => apiClient.updatePreferences(userId!, data),
    onSuccess: () => {
      toast.success('Preference updated');
      queryClient.invalidateQueries({ queryKey: ['preferences'] });
    },
    onError: () => toast.error('Failed to update preference'),
  });

  const togglePref = (key: string, value: boolean, label: string) => {
    updatePrefsMutation.mutate({ [key]: value });
    if (value) toast.success(`${label} enabled`);
    else toast.success(`${label} disabled`);
  };

  // User-preferences keys with UI metadata (list-specific)
  const prefItems = [
    { key: 'autoplay', label: 'Autoplay next episode', desc: 'Automatically play the next episode when the current one ends' },
    { key: 'autoplay_next_episode', label: 'Auto-advance', desc: 'Queue the next episode once one ends' },
    { key: 'closed_captions', label: 'Show subtitles', desc: 'Show captions by default when available' },
    { key: 'allow_explicit_content', label: 'Allow mature content', desc: 'Show content rated for mature audiences' },
    { key: 'email_new_content', label: 'Email notifications', desc: 'Receive updates about new content' },
  ];

  if (!user) {
    return (
      <HomeShell>
        <div className="pt-24 px-4 sm:px-6 lg:px-8 max-w-4xl mx-auto">
          <ProfileSkeleton />
        </div>
      </HomeShell>
    );
  }

  return (
    <HomeShell>
      <div className="pt-24 pb-10 px-4 sm:px-6 lg:px-8 max-w-4xl mx-auto">
        <h1 className="text-3xl font-bold text-white mb-8">Account</h1>

        <Tabs.Root defaultValue="profile" className="space-y-6">
          <Tabs.List className="flex gap-1 bg-dark-900 rounded-lg p-1 border border-dark-800 w-fit">
            {[
              { value: 'profile', label: 'Profile' },
              { value: 'subscription', label: 'Subscription' },
              { value: 'preferences', label: 'Preferences' },
              { value: 'devices', label: 'Devices' },
            ].map((tab) => (
              <Tabs.Trigger
                key={tab.value}
                value={tab.value}
                className="px-4 py-2 text-sm font-medium rounded-md text-gray-400 transition-colors data-[state=active]:bg-dark-800 data-[state=active]:text-white data-[state=inactive]:hover:text-gray-200"
              >
                {tab.label}
              </Tabs.Trigger>
            ))}
          </Tabs.List>

          {/* Profile Tab */}
          <Tabs.Content value="profile" className="animate-fade-in">
            <div className="bg-dark-900 rounded-xl border border-dark-800 p-6">
              <div className="flex items-center justify-between mb-6">
                <h2 className="text-lg font-semibold text-white">Profile Information</h2>
                {!editingProfile ? (
                  <button onClick={() => setEditingProfile(true)} className="text-sm text-red-500 hover:text-red-400 flex items-center gap-1.5 transition-colors">
                    <PencilIcon /> Edit
                  </button>
                ) : (
                  <div className="flex gap-2">
                    <button onClick={() => setEditingProfile(false)} className="text-sm text-gray-400 hover:text-gray-200 transition-colors">Cancel</button>
                    <button onClick={handleSaveProfile} disabled={updateProfileMutation.isPending} className="text-sm text-red-500 hover:text-red-400 font-medium transition-colors disabled:opacity-50">
                      {updateProfileMutation.isPending ? 'Saving...' : 'Save'}
                    </button>
                  </div>
                )}
              </div>

              <div className="flex items-center gap-4 mb-6">
                {profile?.avatar_url ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={profile.avatar_url} alt="" className="w-20 h-20 rounded-full object-cover" />
                ) : (
                  <div className="w-20 h-20 rounded-full bg-gradient-to-br from-red-500 to-red-700 flex items-center justify-center text-white text-2xl font-bold">
                    {user.firstName[0]}{user.lastName[0]}
                  </div>
                )}
                <div>
                  <p className="text-lg font-medium text-white">{user.firstName} {user.lastName}</p>
                  <p className="text-sm text-gray-400">
                    {user.email}
                    {user.emailVerified && <span className="ml-2 text-xs text-green-400">✓ verified</span>}
                  </p>
                </div>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div className="bg-dark-800 rounded-lg p-4">
                  <label className="block text-xs font-medium text-gray-500 mb-1">Country</label>
                  <p className="text-white">{profile?.country || formData.country || '—'}</p>
                </div>
                <div className="bg-dark-800 rounded-lg p-4">
                  <label className="block text-xs font-medium text-gray-500 mb-1">Language</label>
                  <p className="text-white capitalize">{profile?.language || preferences?.language || '—'}</p>
                </div>
                <div className="bg-dark-800 rounded-lg p-4">
                  <label className="block text-xs font-medium text-gray-500 mb-1">Member since</label>
                  <p className="text-white">{profile?.created_at ? new Date(profile.created_at).toLocaleDateString() : '—'}</p>
                </div>
                <div className="bg-dark-800 rounded-lg p-4">
                  <label className="block text-xs font-medium text-gray-500 mb-1">Profile completion</label>
                  <p className="text-white">{profile?.profile_completeness ?? 0}%</p>
                </div>
              </div>
            </div>
          </Tabs.Content>

          {/* Subscription Tab */}
          <Tabs.Content value="subscription" className="animate-fade-in">
            <div className="bg-dark-900 rounded-xl border border-dark-800 p-6">
              <h2 className="text-lg font-semibold text-white mb-6">Subscription</h2>
              {subLoading ? (
                <div className="animate-pulse h-32 bg-dark-800 rounded-lg shimmer" />
              ) : subscription ? (
                <div className="bg-dark-800 rounded-lg p-5 mb-4">
                  <div className="flex items-center justify-between mb-3">
                    <div>
                      <span className="text-lg font-semibold text-white uppercase">{subscription.tier}</span>
                      <span className={`ml-3 text-xs font-medium px-2 py-0.5 rounded-full ${
                        subscription.subscription_status === 'active' ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'
                      }`}>
                        {subscription.subscription_status === 'active' ? 'active' : 'inactive'}
                      </span>
                    </div>
                    {subscription.monthly_price && subscription.tier !== 'avod' ? (
                      <span className="text-xl font-bold text-white">${subscription.monthly_price}
                        <span className="text-sm text-gray-400 font-normal">/mo</span>
                      </span>
                    ) : null}
                  </div>
                  <div className="space-y-2">
                    {(PLAN_FEATURES[subscription.tier] || []).map((feature) => (
                      <div key={feature} className="flex items-center gap-2 text-sm text-gray-300">
                        <CheckIcon /> {feature}
                      </div>
                    ))}
                  </div>
                </div>
              ) : (
                <p className="text-gray-400">No active subscription</p>
              )}
              <Link href="/billing" className="inline-flex items-center gap-2 text-sm text-red-500 hover:text-red-400 transition-colors mt-2">
                Change plan
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
                </svg>
              </Link>
            </div>
          </Tabs.Content>

          {/* Preferences Tab */}
          <Tabs.Content value="preferences" className="animate-fade-in">
            <div className="bg-dark-900 rounded-xl border border-dark-800 p-6 space-y-0">
              <h2 className="text-lg font-semibold text-white mb-6">Preferences</h2>
              {prefItems.map((item, i) => {
                const value = Boolean((preferences as Record<string, unknown> | undefined)?.[item.key]);
                return (
                  <div key={item.key} className={`flex items-center justify-between py-4 ${i > 0 ? 'border-t border-dark-800' : ''}`}>
                    <div>
                      <p className="text-sm font-medium text-white">{item.label}</p>
                      <p className="text-xs text-gray-500">{item.desc}</p>
                    </div>
                    <button
                      onClick={() => togglePref(item.key, !value, item.label)}
                      className={`relative w-11 h-6 rounded-full transition-colors ${value ? 'bg-red-600' : 'bg-dark-600'}`}
                      aria-label={`Toggle ${item.label}`}
                    >
                      <span className={`absolute top-0.5 w-5 h-5 rounded-full bg-white shadow transition-transform ${value ? 'left-[22px]' : 'left-0.5'}`} />
                    </button>
                  </div>
                );
              })}
            </div>
          </Tabs.Content>

          {/* Devices Tab */}
          <Tabs.Content value="devices" className="animate-fade-in">
            <div className="bg-dark-900 rounded-xl border border-dark-800 p-6">
              <h2 className="text-lg font-semibold text-white mb-6">Devices</h2>
              {!devices || devices.length === 0 ? (
                <p className="text-gray-400">No devices registered yet. Devices are added when you stream.</p>
              ) : (
                <div className="space-y-3">
                  {devices.map((device) => (
                    <div key={device.id} className="flex items-center justify-between bg-dark-800 rounded-lg p-4">
                      <div className="flex items-center gap-3">
                        <div className="w-10 h-10 rounded-lg bg-dark-700 flex items-center justify-center">
                          <svg className="w-5 h-5 text-gray-400" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" d="M9 17.25v1.725M12 17.25v1.725M15 17.25v1.725M8.25 14.984a4.5 4.5 0 019 0M8.25 14.984l-2.25 2.25m13.5-2.25l2.25 2.25M3.75 6.75h16.5" />
                          </svg>
                        </div>
                        <div>
                          <p className="text-sm font-medium text-white flex items-center gap-2">
                            {device.device_name || device.device_type}
                            {device.is_active && <span className="text-[10px] font-medium px-1.5 py-0.5 bg-green-500/20 text-green-400 rounded">Active</span>}
                            {device.is_trusted && <span className="text-[10px] font-medium px-1.5 py-0.5 bg-blue-500/20 text-blue-400 rounded">Trusted</span>}
                          </p>
                          <p className="text-xs text-gray-500">
                            {device.device_type}{device.last_active_at ? ` · last active ${new Date(device.last_active_at).toLocaleDateString()}` : ''}
                          </p>
                        </div>
                      </div>
                      {!device.is_active && (
                        <button
                          onClick={() => toast.success('Device signed out')}
                          className="text-xs text-red-400 hover:text-red-300 transition-colors"
                        >
                          Remove
                        </button>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </Tabs.Content>
        </Tabs.Root>
      </div>
    </HomeShell>
  );
}