'use client';

import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { apiClient } from '@/api/client';
import { ContentCard } from '@/components/browse/ContentCard';
import { ContentGridSkeleton } from '@/components/common/Skeleton';
import { useIsAuthenticated } from '@/hooks';
import { useRouter } from 'next/navigation';
import { Content } from '@/types';

export default function BrowsePage() {
  const isAuthenticated = useIsAuthenticated();
  const router = useRouter();
  const [searchQuery, setSearchQuery] = useState('');

  useEffect(() => {
    if (!isAuthenticated) {
      router.push('/login');
    }
  }, [isAuthenticated, router]);

  const { data: moviesData, isLoading: moviesLoading } = useQuery({
    queryKey: ['movies'],
    queryFn: () => apiClient.getMovies(20),
    enabled: isAuthenticated,
  });

  const { data: trendingData, isLoading: trendingLoading } = useQuery({
    queryKey: ['trending'],
    queryFn: () => apiClient.getTrending(),
    enabled: isAuthenticated,
  });

  const { data: searchData, isLoading: searchLoading } = useQuery({
    queryKey: ['search', searchQuery],
    queryFn: () => apiClient.searchContent(searchQuery),
    enabled: isAuthenticated && searchQuery.length > 0,
  });

  const movies: Content[] = searchQuery ? searchData?.data?.results || [] : moviesData?.data?.movies || [];

  return (
    <div className="min-h-screen bg-black">
      <div className="max-w-7xl mx-auto px-4 py-8">
        {/* Search Bar */}
        <div className="mb-8">
          <input
            type="text"
            placeholder="Search movies and shows..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full px-4 py-3 rounded-lg bg-gray-800 text-white placeholder-gray-400"
          />
        </div>

        {/* Trending Section */}
        {!searchQuery && (
          <section className="mb-12">
            <h2 className="text-2xl font-bold mb-4">Trending Now</h2>
            {trendingLoading ? (
              <ContentGridSkeleton count={4} />
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-3 lg:grid-cols-4 gap-4">
                {trendingData?.data?.map((item: Content) => (
                  <ContentCard key={item.id} content={item} />
                ))}
              </div>
            )}
          </section>
        )}

        {/* Movies/Search Results */}
        <section>
          <h2 className="text-2xl font-bold mb-4">
            {searchQuery ? 'Search Results' : 'Movies'}
          </h2>
          {moviesLoading || searchLoading ? (
            <ContentGridSkeleton count={8} />
          ) : movies.length > 0 ? (
            <div className="grid grid-cols-1 md:grid-cols-3 lg:grid-cols-4 gap-4">
              {movies.map((movie: Content) => (
                <ContentCard key={movie.id} content={movie} />
              ))}
            </div>
          ) : (
            <p className="text-gray-400">No results found</p>
          )}
        </section>
      </div>
    </div>
  );
}
