'use client';

import { useEffect, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { useQuery } from '@tanstack/react-query';
import { apiClient } from '@/api/client';
import { useIsAuthenticated, useUser } from '@/hooks';
import { MediaCard } from '@/components/browse/MediaCard';
import { Content, Content as ContentType } from '@/types';
import { toast } from 'sonner';
import dynamic from 'next/dynamic';
import Link from 'next/link';

// Dynamically import VideoPlayer to avoid SSR issues with hls.js/dashjs
const VideoPlayer = dynamic(
  () => import('@/components/player/VideoPlayer').then((mod) => mod.VideoPlayer),
  { ssr: false }
);

export default function WatchPage() {
  const params = useParams();
  const contentId = params.id as string;
  const isAuthenticated = useIsAuthenticated();
  const user = useUser();
  const router = useRouter();
  const [sessionId, setSessionId] = useState<string>('');
  const [isStarting, setIsStarting] = useState(true);

  useEffect(() => {
    if (!isAuthenticated) {
      router.push('/login');
      return;
    }

    const startSession = async () => {
      try {
        const response = await apiClient.startStreaming(contentId, 'web-player');
        setSessionId(response.data.session_id);
        setIsStarting(false);
      } catch (error) {
        toast.error('Failed to start playback. Please try again.');
        router.push('/browse');
      }
    };

    startSession();
  }, [contentId, isAuthenticated, router]);

  // Fetch content details
  const { data: contentData } = useQuery({
    queryKey: ['content', contentId],
    queryFn: () => apiClient.getContentById(contentId),
    enabled: isAuthenticated,
  });

  const content: Content | null = contentData?.data || null;

  // Fetch continue watching / watch history for related content
  const { data: historyData } = useQuery({
    queryKey: ['watch-history', user?.id],
    queryFn: () => user ? apiClient.getWatchHistory(user.id) : Promise.reject(new Error('No user')),
    enabled: isAuthenticated && !!user,
  });

  // Fetch recommendations
  const { data: similarData } = useQuery({
    queryKey: ['recommendations', contentId],
    queryFn: () => apiClient.getRecommendations(contentId, 12),
    enabled: isAuthenticated,
  });

  const similarContent: ContentType[] = similarData?.data || [];
  const historyItems: ContentType[] = historyData?.data?.items || [];

  if (isStarting) {
    return (
      <div className="min-h-screen bg-dark-950 flex items-center justify-center">
        <div className="flex flex-col items-center gap-4">
          <div className="w-10 h-10 border-2 border-red-600 border-t-transparent rounded-full animate-spin" />
          <p className="text-gray-400 text-sm">Loading player...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-dark-950">
      {/* Video Player */}
      <div className="w-full max-h-[80vh] bg-black">
        <VideoPlayer contentId={contentId} sessionId={sessionId} />
      </div>

      {/* Content Info */}
      <div className="max-w-6xl mx-auto px-4 sm:px-6 py-6">
        {content && (
          <div className="mb-8">
            <div className="flex items-start justify-between gap-4 mb-4">
              <div>
                <h1 className="text-2xl sm:text-3xl font-bold text-white mb-2">{content.title}</h1>
                <div className="flex items-center gap-3 text-sm text-gray-400">
                  {content.rating > 0 && (
                    <span className="flex items-center gap-1">
                      <svg className="w-4 h-4 text-yellow-500" fill="currentColor" viewBox="0 0 20 20">
                        <path d="M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.07 3.292a1 1 0 00.95.69h3.462c.969 0 1.371 1.24.588 1.81l-2.8 2.034a1 1 0 00-.364 1.118l1.07 3.292c.3.921-.755 1.688-1.54 1.118l-2.8-2.034a1 1 0 00-1.175 0l-2.8 2.034c-.784.57-1.838-.197-1.539-1.118l1.07-3.292a1 1 0 00-.364-1.118L2.98 8.72c-.783-.57-.38-1.81.588-1.81h3.461a1 1 0 00.951-.69l1.07-3.292z" />
                      </svg>
                      {content.rating.toFixed(1)}
                    </span>
                  )}
                  <span>{content.releaseDate?.split('-')[0]}</span>
                  {content.duration > 0 && (
                    <span>{Math.floor(content.duration / 60)}h {content.duration % 60}m</span>
                  )}
                  <span className="uppercase text-xs font-medium px-2 py-0.5 border border-dark-600 rounded text-gray-400">
                    {content.type}
                  </span>
                </div>
              </div>
              <div className="flex items-center gap-2 flex-shrink-0">
                <button
                  onClick={() => toast.success('Added to My List')}
                  className="p-2 rounded-full bg-dark-800 hover:bg-dark-700 text-gray-300 hover:text-white transition-colors"
                  aria-label="Add to my list"
                >
                  <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M12 4.5v15m7.5-7.5h-15" />
                  </svg>
                </button>
                <button
                  className="p-2 rounded-full bg-dark-800 hover:bg-dark-700 text-gray-300 hover:text-white transition-colors"
                  aria-label="Share"
                >
                  <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M7.217 21.75a.923.923 0 00-.284-.075.913.913 0 01-.097-.023l-3.446-1.38a.915.915 0 01-.534-1.186l1.38-3.446a.913.913 0 01.097-.023.923.923 0 00.284-.075M7.217 21.75l3.033-3.033M7.217 21.75A.912.912 0 017 21.75H4.5a.912.912 0 01-.912-.912V18.25M10.25 18.75l3.033 3.033M10.25 18.75l-3.033-3.033M16.783 2.25a.923.923 0 00.284.075.913.913 0 01.097.023l3.446 1.38a.915.915 0 01.534 1.186l-1.38 3.446a.913.913 0 01-.097.023.923.923 0 00-.284.075M16.783 2.25l-3.033 3.033M16.783 2.25A.912.912 0 0117 2.25h2.5a.912.912 0 01.912.912V5.75M13.75 5.75l-3.033-3.033M13.75 5.75l3.033 3.033" />
                  </svg>
                </button>
              </div>
            </div>
            <p className="text-gray-400 text-sm leading-relaxed max-w-2xl">
              {content.description}
            </p>
            <div className="mt-3 text-sm text-gray-500">
              <span className="text-gray-400">Genre:</span> {content.genre}
            </div>
          </div>
        )}

        {/* Episodes / Seasons Placeholder (for shows) */}
        {content?.type === 'show' && (
          <div className="mb-8">
            <h2 className="text-lg font-bold text-white mb-4">Episodes</h2>
            <div className="space-y-2">
              {[1, 2, 3, 4, 5, 6].map((ep) => (
                <div
                  key={ep}
                  className="flex items-center gap-4 p-3 rounded-lg hover:bg-dark-800 transition-colors cursor-pointer group"
                >
                  <span className="text-sm text-gray-500 w-8 text-center">{ep}</span>
                  <div className="w-28 h-16 bg-dark-800 rounded-md overflow-hidden flex-shrink-0">
                    <div className="w-full h-full bg-dark-700 shimmer" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm text-white font-medium truncate group-hover:text-red-500 transition-colors">
                      Episode {ep}: {content.title}
                    </p>
                    <p className="text-xs text-gray-500">{Math.floor(Math.random() * 20 + 35)}m</p>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* More Like This */}
        {similarContent.length > 0 && (
          <div className="mb-8">
            <h2 className="text-lg font-bold text-white mb-4">More Like This</h2>
            <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-3">
              {similarContent.map((item: ContentType) => (
                <MediaCard key={item.id} content={item} />
              ))}
            </div>
          </div>
        )}

        {/* Continue Watching */}
        {historyItems.length > 0 && (
          <div>
            <h2 className="text-lg font-bold text-white mb-4">Continue Watching</h2>
            <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-3">
              {historyItems.slice(0, 5).map((item: ContentType) => (
                <MediaCard key={item.id} content={item} variant="backdrop" />
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Back to Browse */}
      <div className="px-4 sm:px-6 pb-8">
        <Link
          href="/browse"
          className="inline-flex items-center gap-2 text-sm text-gray-400 hover:text-white transition-colors"
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" d="M10.5 19.5L3 12m0 0l7.5-7.5M3 12h18" />
          </svg>
          Back to Browse
        </Link>
      </div>
    </div>
  );
}
