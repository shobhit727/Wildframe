'use client';

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { apiClient } from '@/api/client';
import { MediaCard } from '@/components/browse/MediaCard';
import { ContentGridSkeleton } from '@/components/common/Skeleton';
import { HomeShell } from '@/components/layout/HomeShell';
import { useIsAuthenticated, useUser } from '@/hooks';
import { Content, StreamingSession } from '@/types';

export default function MyListPage() {
  const isAuthenticated = useIsAuthenticated();
  const user = useUser();
  const [activeTab, setActiveTab] = useState<'history' | 'watchlist'>('history');

  // Watch History
  const { data: historyData, isLoading: historyLoading } = useQuery({
    queryKey: ['watch-history', user?.id],
    queryFn: () => user ? apiClient.getWatchHistory(user.id) : Promise.reject(new Error('No user')),
    enabled: isAuthenticated && !!user,
  });

  // Recommendations as a "for you" list
  const { data: recommendedData, isLoading: recommendedLoading } = useQuery({
    queryKey: ['recommendations', user?.id],
    queryFn: () => user ? apiClient.getRecommendations(user.id, 30) : Promise.reject(new Error('No user')),
    enabled: isAuthenticated && !!user && activeTab === 'watchlist',
  });

  // Trending as backup watchlist content
  const { data: trendingData } = useQuery({
    queryKey: ['trending'],
    queryFn: () => apiClient.getTrending(),
    enabled: isAuthenticated,
  });

  const historyItems: Content[] = historyData?.data?.items || [];
  const watchlistItems: Content[] = recommendedData?.data || [];
  const trendingItems: Content[] = trendingData?.data || [];

  // Compute watch progress from history
  const watchProgress: Record<string, number> = {};
  if (historyData?.data?.sessions) {
    for (const session of historyData.data.sessions as StreamingSession[]) {
      if (session.currentPosition > 0) {
        watchProgress[session.contentId] = Math.min(
          (session.currentPosition / (session.currentPosition + 600)) * 100,
          95
        );
      }
    }
  }

  const tabs = [
    { id: 'history' as const, label: 'Continue Watching', count: historyItems.length },
    { id: 'watchlist' as const, label: 'My List', count: watchlistItems.length },
  ];

  return (
    <HomeShell>
      <div className="pt-24 pb-10 px-4 sm:px-6 lg:px-8 max-w-7xl mx-auto">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <h1 className="text-3xl font-bold text-white">My Library</h1>
        </div>

        {/* Tab Switcher */}
        <div className="flex gap-1 bg-dark-900 rounded-lg p-1 border border-dark-800 w-fit mb-8">
          {tabs.map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`px-5 py-2 text-sm font-medium rounded-md transition-colors ${
                activeTab === tab.id
                  ? 'bg-dark-800 text-white'
                  : 'text-gray-400 hover:text-gray-200'
              }`}
            >
              {tab.label}
              {tab.count > 0 && (
                <span className="ml-2 text-xs text-gray-500">({tab.count})</span>
              )}
            </button>
          ))}
        </div>

        {/* Content */}
        {activeTab === 'history' ? (
          // Continue Watching
          historyLoading ? (
            <ContentGridSkeleton count={8} />
          ) : historyItems.length > 0 ? (
            <>
              {/* Continue Watching - backdrop style with progress */}
              <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-4">
                {historyItems.map((item: Content) => (
                  <MediaCard
                    key={item.id}
                    content={item}
                    variant="backdrop"
                    showProgress={watchProgress[item.id]}
                  />
                ))}
              </div>

              {/* Also Trending */}
              {trendingItems.length > 0 && (
                <div className="mt-12">
                  <h2 className="text-xl font-bold text-white mb-4">Trending Now</h2>
                  <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-3">
                    {trendingItems.slice(0, 12).map((item: Content) => (
                      <MediaCard key={item.id} content={item} />
                    ))}
                  </div>
                </div>
              )}
            </>
          ) : (
            <div className="text-center py-20">
              <svg className="w-16 h-16 mx-auto text-gray-700 mb-4" fill="none" viewBox="0 0 24 24" strokeWidth={1} stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 6v6h4.5m4.5 0a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
              <h3 className="text-lg font-medium text-gray-400 mb-2">Nothing watched yet</h3>
              <p className="text-sm text-gray-600 mb-6">Start watching to see your progress here.</p>
              <a
                href="/browse"
                className="inline-flex items-center gap-2 bg-red-600 hover:bg-red-700 text-white text-sm font-medium px-6 py-2.5 rounded-lg transition-colors"
              >
                Browse Content
              </a>
            </div>
          )
        ) : (
          // My List / Watchlist
          recommendedLoading ? (
            <ContentGridSkeleton count={8} />
          ) : watchlistItems.length > 0 ? (
            <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-3">
              {watchlistItems.map((item: Content) => (
                <MediaCard key={item.id} content={item} />
              ))}
            </div>
          ) : (
            <div className="text-center py-20">
              <svg className="w-16 h-16 mx-auto text-gray-700 mb-4" fill="none" viewBox="0 0 24 24" strokeWidth={1} stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 4.5v15m7.5-7.5h-15" />
              </svg>
              <h3 className="text-lg font-medium text-gray-400 mb-2">Your list is empty</h3>
              <p className="text-sm text-gray-600 mb-6">Add movies and shows to your list to watch later.</p>
              <a
                href="/browse"
                className="inline-flex items-center gap-2 bg-red-600 hover:bg-red-700 text-white text-sm font-medium px-6 py-2.5 rounded-lg transition-colors"
              >
                Browse Content
              </a>
            </div>
          )
        )}
      </div>
    </HomeShell>
  );
}
