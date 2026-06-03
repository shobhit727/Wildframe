'use client';

import { useQuery } from '@tanstack/react-query';
import { apiClient } from '@/api/client';
import { ContentCard } from '@/components/browse/ContentCard';
import { ContentGridSkeleton } from '@/components/common/Skeleton';
import { useIsAuthenticated, useUser } from '@/hooks';
import { useRouter } from 'next/navigation';
import { useEffect } from 'react';
import { Content } from '@/types';

export default function MyListPage() {
  const isAuthenticated = useIsAuthenticated();
  const user = useUser();
  const router = useRouter();

  useEffect(() => {
    if (!isAuthenticated) {
      router.push('/login');
    }
  }, [isAuthenticated, router]);

  const { data: historyData, isLoading } = useQuery({
    queryKey: ['watch-history', user?.id],
    queryFn: () => user ? apiClient.getWatchHistory(user.id) : Promise.reject(),
    enabled: isAuthenticated && !!user,
  });

  const history: Content[] = historyData?.data?.items || [];

  return (
    <div className="min-h-screen bg-black">
      <div className="max-w-7xl mx-auto px-4 py-8">
        <h1 className="text-3xl font-bold mb-8">My Watch History</h1>

        {isLoading ? (
          <ContentGridSkeleton count={8} />
        ) : history.length > 0 ? (
          <div className="grid grid-cols-1 md:grid-cols-3 lg:grid-cols-4 gap-4">
            {history.map((item: Content) => (
              <ContentCard key={item.id} content={item} />
            ))}
          </div>
        ) : (
          <p className="text-gray-400">No watch history yet</p>
        )}
      </div>
    </div>
  );
}
