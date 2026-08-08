'use client';

import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { apiClient, normalizeContent } from '@/api/client';
import { MediaCard } from '@/components/browse/MediaCard';
import { ContentGridSkeleton } from '@/components/common/Skeleton';
import { HomeShell } from '@/components/layout/HomeShell';
import { useIsAuthenticated, useUser } from '@/hooks';
import { Content } from '@/types';

const MY_LIST_KEY = 'wildframe_my_list';

function readMyList(): string[] {
  if (typeof window === 'undefined') return [];
  try {
    return JSON.parse(localStorage.getItem(MY_LIST_KEY) || '[]') as string[];
  } catch {
    return [];
  }
}

export default function MyListPage() {
  const isAuthenticated = useIsAuthenticated();
  const user = useUser();
  const [activeTab, setActiveTab] = useState<'history' | 'watchlist'>('history');
  const [listVersion, setListVersion] = useState(0);

  // Continue Watching — real playback sessions from streaming-service
  const { data: historyData, isLoading: historyLoading } = useQuery({
    queryKey: ['watch-history', user?.id],
    queryFn: () => (user ? apiClient.getWatchHistory(user.id) : Promise.resolve([])),
    enabled: isAuthenticated && !!user,
  });

  // My List — local watchlist (no backend watchlist endpoint yet)
  const cached = useMemo(() => readMyList(), []);
  const { data: myListData, isLoading: myListLoading } = useQuery({
    queryKey: ['my-list', cached.join(','), listVersion],
    queryFn: async () => {
      const ids = readMyList();
      const items = await Promise.all(
        ids.map(async (id) => {
          try {
            return await apiClient.getContentById(id);
          } catch {
            return null;
          }
        })
      );
      return items.filter(Boolean) as Awaited<ReturnType<typeof apiClient.getContentById>>[];
    },
    enabled: isAuthenticated && cached.length > 0,
  });

  const continueWatching = useMemo(
    () =>
      (historyData || [])
        .filter((h) => h.content)
        .map((h) => ({
          content: normalizeContent(h.content!),
          progress: h.session.total_duration_seconds > 0
            ? (h.session.current_position_seconds / h.session.total_duration_seconds) * 100
            : h.session.current_position_seconds > 0 ? 10 : 0,
        })),
    [historyData]
  );

  const myList: Content[] = useMemo(() => (myListData || []).map(normalizeContent), [myListData]);

  const removeFromList = (id: string) => {
    const list = readMyList().filter((x) => x !== id);
    localStorage.setItem(MY_LIST_KEY, JSON.stringify(list));
    setListVersion((v) => v + 1);
  };

  const tabs = [
    { id: 'history' as const, label: 'Continue Watching', count: continueWatching.length },
    { id: 'watchlist' as const, label: 'My List', count: myList.length },
  ];

  return (
    <HomeShell>
      <div className="pt-24 pb-10 px-8">
        <div className="max-w-7xl mx-auto">
          <h1 className="text-3xl sm:text-4xl font-semibold text-white mb-6">My List</h1>

          {/* Tabs */}
          <div className="flex gap-6 mb-8 border-b border-white/10">
            {tabs.map((tab) => (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`pb-3 text-sm text-base font-medium transition-colors ${
                  activeTab === tab.id
                    ? 'text-white'
                    : 'text-gray-400 hover:text-white'
                }`}
              >
                {tab.label}
                {tab.count > 0 && (
                  <span className="ml-2 text-xs text-[#E50914]">({tab.count})</span>
                )}
              </button>
            ))}
          </div>

          {activeTab === 'history' ? (
            <div>
              {historyLoading ? (
                <ContentGridSkeleton count={12} />
              ) : continueWatching.length > 0 ? (
                <>
                  <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-4">
                    {continueWatching.map(({ content, progress }) => (
                      <MediaCard
                        key={content.id}
                        content={content}
                        variant="backdrop"
                        showProgress={Math.min(progress, 95)}
                        showCaption
                      />
                    ))}
                  </div>
                  <p className="mt-4 text-xs text-gray-500">
                    Resume playback and your position saves automatically.
                  </p>
                </>
              ) : (
                <EmptyState
                  emoji="🎬"
                  title="Nothing in progress"
                  subtitle="Start watching a title and it will show up here."
                />
              )}
            </div>
          ) : (
            <div>
              {myListLoading ? (
                <ContentGridSkeleton count={12} />
              ) : myList.length > 0 ? (
                <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-4">
                  {myList.map((item) => (
                    <div key={item.id} className="relative group">
                      <MediaCard content={item} showCaption />
                      <button
                        onClick={() => removeFromList(item.id)}
                        className="absolute top-2 left-2 bg-black/70 text-gray-200 hover:text-white text-xs px-2 py-1 opacity-0 group-hover:opacity-100 transition-opacity"
                        aria-label={`Remove ${item.title} from My List`}
                      >
                        Remove ✕
                      </button>
                    </div>
                  ))}
                </div>
              ) : (
                <EmptyState
                  emoji="📺"
                  title="Your list is empty"
                  subtitle="Tap the My List button on any title to save it here."
                />
              )}
            </div>
          )}
        </div>
      </div>
    </HomeShell>
  );
}

function EmptyState({ emoji, title, subtitle }: { emoji: string; title: string; subtitle: string }) {
  return (
    <div className="text-center py-20">
      <div className="text-5xl mb-4">{emoji}</div>
      <h3 className="text-lg font-medium text-gray-300">{title}</h3>
      <p className="text-sm text-gray-500 mt-1">{subtitle}</p>
    </div>
  );
}