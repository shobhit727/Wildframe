'use client';

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { apiClient } from '@/api/client';
import { HomeShell } from '@/components/layout/HomeShell';
import { HeroBanner } from '@/components/browse/HeroBanner';
import { Row } from '@/components/browse/Row';
import { MediaCard } from '@/components/browse/MediaCard';
import { HeroSkeleton, RowSkeleton, ContentGridSkeleton } from '@/components/common/Skeleton';
import { useIsAuthenticated, useUser } from '@/hooks';
import { Content } from '@/types';

export default function BrowsePage() {
  const isAuthenticated = useIsAuthenticated();
  const user = useUser();
  const [searchQuery, setSearchQuery] = useState('');

  const { data: moviesData, isLoading: moviesLoading } = useQuery({
    queryKey: ['movies'],
    queryFn: () => apiClient.getMovies(30),
    enabled: isAuthenticated,
  });

  const { data: showsData, isLoading: showsLoading } = useQuery({
    queryKey: ['shows'],
    queryFn: () => apiClient.getShows(30),
    enabled: isAuthenticated,
  });

  const { data: trendingData, isLoading: trendingLoading } = useQuery({
    queryKey: ['trending'],
    queryFn: () => apiClient.getTrending(),
    enabled: isAuthenticated,
  });

  const { data: recommendedData, isLoading: recommendedLoading } = useQuery({
    queryKey: ['recommendations', user?.id],
    queryFn: () => user ? apiClient.getRecommendations(user.id, 20) : Promise.reject(new Error('No user')),
    enabled: isAuthenticated && !!user,
  });

  const { data: searchData, isLoading: searchLoading } = useQuery({
    queryKey: ['search', searchQuery],
    queryFn: () => apiClient.searchContent(searchQuery),
    enabled: isAuthenticated && searchQuery.length >= 2,
  });

  const movies: Content[] = moviesData?.data?.movies || [];
  const shows: Content[] = showsData?.data?.shows || [];
  const trending: Content[] = trendingData?.data || [];
  const recommended: Content[] = recommendedData?.data || [];
  const searchResults: Content[] = searchData?.data?.results || [];

  // Group movies by genre for genre rows
  const genreRows: Record<string, Content[]> = {};
  const allContent = [...movies, ...shows];
  for (const item of allContent) {
    const genre = item.genre || 'Other';
    if (!genreRows[genre]) genreRows[genre] = [];
    genreRows[genre].push(item);
  }

  const isSearchActive = searchQuery.length >= 2;

  return (
    <HomeShell onSearchChange={setSearchQuery}>
      <div className="pt-0">
        {isSearchActive ? (
          /* Search Results View */
          <div className="pt-24 pb-10 px-4 sm:px-6 lg:px-8">
            <h2 className="text-2xl font-bold text-white mb-6">
              Results for &ldquo;{searchQuery}&rdquo;
            </h2>
            {searchLoading ? (
              <ContentGridSkeleton count={12} />
            ) : searchResults.length > 0 ? (
              <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-3">
                {searchResults.map((item: Content) => (
                  <MediaCard key={item.id} content={item} />
                ))}
              </div>
            ) : (
              <div className="text-center py-20">
                <svg className="w-16 h-16 mx-auto text-gray-600 mb-4" fill="none" viewBox="0 0 24 24" strokeWidth={1} stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-5.197-5.197m0 0A7.5 7.5 0 105.196 5.196a7.5 7.5 0 0010.607 10.607z" />
                </svg>
                <h3 className="text-lg font-medium text-gray-400 mb-1">No results found</h3>
                <p className="text-sm text-gray-600">Try searching for a different title or genre.</p>
              </div>
            )}
          </div>
        ) : (
          /* Browse View */
          <>
            {/* Hero Banner */}
            {trendingLoading ? (
              <HeroSkeleton />
            ) : (
              <HeroBanner items={trending.slice(0, 5)} />
            )}

            <div className="-mt-16 relative z-10 space-y-8 pb-10">
              {/* Trending Row */}
              <Row title="Trending Now" items={trending} variant="backdrop" />

              {/* Recommended For You */}
              {user && (recommendedLoading ? (
                <RowSkeleton />
              ) : recommended.length > 0 ? (
                <Row title="Recommended For You" items={recommended} />
              ) : null)}

              {/* Movies Row */}
              {moviesLoading ? (
                <RowSkeleton />
              ) : (
                <Row title="Popular Movies" items={movies} />
              )}

              {/* Shows Row */}
              {showsLoading ? (
                <RowSkeleton />
              ) : (
                <Row title="TV Shows" items={shows} variant="backdrop" />
              )}

              {/* Genre Rows */}
              {Object.entries(genreRows)
                .filter(([, items]) => items.length >= 3)
                .sort(([a], [b]) => a.localeCompare(b))
                .map(([genre, items]) => (
                  <Row key={genre} title={genre} items={items} />
                ))}
            </div>
          </>
        )}
      </div>
    </HomeShell>
  );
}
