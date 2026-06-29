'use client';

import { useUser } from '@/hooks';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '@/api/client';
import { HomeShell } from '@/components/layout/HomeShell';
import { ProfileSkeleton } from '@/components/common/Skeleton';
import { Subscription } from '@/types';
import { toast } from 'sonner';
import { useState } from 'react';
import Link from 'next/link';
import * as Tabs from '@radix-ui/react-tabs';

// Inline SVG icon helper
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

export default function AccountPage() {
  const user = useUser();
  const queryClient = useQueryClient();
  const [editingProfile, setEditingProfile] = useState(false);
  const [formData, setFormData] = useState({
    firstName: user?.firstName || '',
    lastName: user?.lastName || '',
    email: user?.email || '',
  });

  // Sync form data when user loads
  if (user && !editingProfile && formData.firstName !== user.firstName) {
    setFormData({
      firstName: user.firstName,
      lastName: user.lastName,
      email: user.email,
    });
  }

  const { data: subscriptionData, isLoading: subLoading } = useQuery({
    queryKey: ['subscription', user?.id],
    queryFn: () => user ? apiClient.getSubscription(user.id) : Promise.reject(new Error('No user')),
    enabled: !!user,
  });

  const subscription: Subscription | null = subscriptionData?.data || null;

  const updateProfileMutation = useMutation({
    mutationFn: (data: Record<string, unknown>) => apiClient.updateProfile(data),
    onSuccess: () => {
      toast.success('Profile updated');
      setEditingProfile(false);
      queryClient.invalidateQueries({ queryKey: ['profile'] });
    },
    onError: () => toast.error('Failed to update profile'),
  });

  const handleSaveProfile = () => {
    updateProfileMutation.mutate({
      first_name: formData.firstName,
      last_name: formData.lastName,
    });
  };

  // Preferences (local state - could be backed by API)
  const [preferences, setPreferences] = useState({
    autoplay: true,
    subtitles: false,
    matureContent: false,
    emailNotifications: true,
  });

  const togglePref = (key: keyof typeof preferences) => {
    setPreferences((prev) => ({ ...prev, [key]: !prev[key] }));
    toast.success('Preference updated');
  };

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
          {/* Tab Nav */}
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

              {/* Avatar */}
              <div className="flex items-center gap-4 mb-6">
                <div className="w-20 h-20 rounded-full bg-gradient-to-br from-red-500 to-red-700 flex items-center justify-center text-white text-2xl font-bold">
                  {user.firstName[0]}{user.lastName[0]}
                </div>
                <div>
                  <p className="text-lg font-medium text-white">{user.firstName} {user.lastName}</p>
                  <p className="text-sm text-gray-400">{user.email}</p>
                </div>
              </div>

              {editingProfile ? (
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                  <div>
                    <label className="block text-sm font-medium text-gray-400 mb-2">First Name</label>
                    <input
                      type="text"
                      value={formData.firstName}
                      onChange={(e) => setFormData((p) => ({ ...p, firstName: e.target.value }))}
                      className="w-full bg-dark-800 text-white px-4 py-2.5 rounded-lg border border-dark-600 focus:outline-none focus:ring-2 focus:ring-red-600/50"
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-400 mb-2">Last Name</label>
                    <input
                      type="text"
                      value={formData.lastName}
                      onChange={(e) => setFormData((p) => ({ ...p, lastName: e.target.value }))}
                      className="w-full bg-dark-800 text-white px-4 py-2.5 rounded-lg border border-dark-600 focus:outline-none focus:ring-2 focus:ring-red-600/50"
                    />
                  </div>
                </div>
              ) : (
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                  <div className="bg-dark-800 rounded-lg p-4">
                    <label className="block text-xs font-medium text-gray-500 mb-1">First Name</label>
                    <p className="text-white">{user.firstName}</p>
                  </div>
                  <div className="bg-dark-800 rounded-lg p-4">
                    <label className="block text-xs font-medium text-gray-500 mb-1">Last Name</label>
                    <p className="text-white">{user.lastName}</p>
                  </div>
                  <div className="bg-dark-800 rounded-lg p-4 sm:col-span-2">
                    <label className="block text-xs font-medium text-gray-500 mb-1">Email</label>
                    <p className="text-white">{user.email}</p>
                  </div>
                </div>
              )}
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
                      <span className="text-lg font-semibold text-white capitalize">{subscription.tier}</span>
                      <span className={`ml-3 text-xs font-medium px-2 py-0.5 rounded-full ${
                        subscription.status === 'active' ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'
                      }`}>
                        {subscription.status}
                      </span>
                    </div>
                    <span className="text-xl font-bold text-white">${subscription.price}<span className="text-sm text-gray-400 font-normal">/mo</span></span>
                  </div>
                  <div className="space-y-2">
                    {subscription.features.map((feature) => (
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
              {[
                { key: 'autoplay' as const, label: 'Autoplay next episode', desc: 'Automatically play the next episode when the current one ends' },
                { key: 'subtitles' as const, label: 'Show subtitles', desc: 'Show subtitles by default when available' },
                { key: 'matureContent' as const, label: 'Allow mature content', desc: 'Show content rated for mature audiences' },
                { key: 'emailNotifications' as const, label: 'Email notifications', desc: 'Receive updates about new content and account activity' },
              ].map((item, i) => (
                <div key={item.key} className={`flex items-center justify-between py-4 ${i > 0 ? 'border-t border-dark-800' : ''}`}>
                  <div>
                    <p className="text-sm font-medium text-white">{item.label}</p>
                    <p className="text-xs text-gray-500">{item.desc}</p>
                  </div>
                  <button
                    onClick={() => togglePref(item.key)}
                    className={`relative w-11 h-6 rounded-full transition-colors ${preferences[item.key] ? 'bg-red-600' : 'bg-dark-600'}`}
                    aria-label={`Toggle ${item.label}`}
                  >
                    <span className={`absolute top-0.5 w-5 h-5 rounded-full bg-white shadow transition-transform ${preferences[item.key] ? 'left-[22px]' : 'left-0.5'}`} />
                  </button>
                </div>
              ))}
            </div>
          </Tabs.Content>

          {/* Devices Tab */}
          <Tabs.Content value="devices" className="animate-fade-in">
            <div className="bg-dark-900 rounded-xl border border-dark-800 p-6">
              <h2 className="text-lg font-semibold text-white mb-6">Active Devices</h2>
              <div className="space-y-3">
                {[
                  { name: 'Chrome - Mac', location: 'San Francisco, CA', current: true, lastActive: 'Now' },
                  { name: 'Safari - iPhone', location: 'San Francisco, CA', current: false, lastActive: '2 hours ago' },
                  { name: 'Smart TV - Living Room', location: 'San Francisco, CA', current: false, lastActive: '3 days ago' },
                ].map((device) => (
                  <div key={device.name} className="flex items-center justify-between bg-dark-800 rounded-lg p-4">
                    <div className="flex items-center gap-3">
                      <div className="w-10 h-10 rounded-lg bg-dark-700 flex items-center justify-center">
                        <svg className="w-5 h-5 text-gray-400" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                          <path strokeLinecap="round" strokeLinejoin="round" d="M9 17.25v1.725M12 17.25v1.725M15 17.25v1.725M8.25 14.984a4.5 4.5 0 019 0M8.25 14.984l-2.25 2.25m13.5-2.25l2.25 2.25M3.75 6.75h16.5" />
                        </svg>
                      </div>
                      <div>
                        <p className="text-sm font-medium text-white flex items-center gap-2">
                          {device.name}
                          {device.current && <span className="text-[10px] font-medium px-1.5 py-0.5 bg-green-500/20 text-green-400 rounded">Current</span>}
                        </p>
                        <p className="text-xs text-gray-500">{device.location} - {device.lastActive}</p>
                      </div>
                    </div>
                    {!device.current && (
                      <button
                        onClick={() => toast.success('Device removed')}
                        className="text-xs text-red-400 hover:text-red-300 transition-colors"
                      >
                        Remove
                      </button>
                    )}
                  </div>
                ))}
              </div>
            </div>
          </Tabs.Content>
        </Tabs.Root>
      </div>
    </HomeShell>
  );
}
