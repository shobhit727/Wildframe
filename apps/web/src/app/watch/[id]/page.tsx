'use client';

import { useEffect, useMemo, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { useQuery } from '@tanstack/react-query';
import { apiClient, normalizeContent, DEMO_HLS_URL } from '@/api/client';
import { useIsAuthenticated, useUser } from '@/hooks';
import { MediaCard } from '@/components/browse/MediaCard';
import { Content } from '@/types';
import { toast } from 'sonner';
import dynamic from 'next/dynamic';
import Link from 'next/link';

// Dynamically import VideoPlayer to avoid SSR issues with hls.js/dashjs
const VideoPlayer = dynamic(
  () => import('@/components/player/VideoPlayer').then((mod) => mod.VideoPlayer),
  { ssr: false }
);

const MY_LIST_KEY = 'wildframe_my_list';

function isInMyList(contentId: string): boolean {
  if (typeof window === 'undefined') return false;
  try {
    return (JSON.parse(localStorage.getItem(MY_LIST_KEY) || '[]') as string[]).includes(contentId);
  } catch {
    return false;
  }
}

export default function WatchPage() {
  const params = useParams();
  const contentId = params.id as string;
  const isAuthenticated = useIsAuthenticated();
  const user = useUser();
  const router = useRouter();
  const [sessionId, setSessionId] = useState<string>('');
  const [selectedEpisode, setSelectedEpisode] = useState<{ id: string; number: number } | null>(null);
  const [streamUrl, setStreamUrl] = useState<string | null>(null);
  const [isStarting, setIsStarting] = useState(true);
  const [inMyList, setInMyList] = useState(false);

  // Fetch content details
  const { data: contentData } = useQuery({
    queryKey: ['content', contentId],
    queryFn: () => apiClient.getContentById(contentId),
    enabled: isAuthenticated && !!contentId,
  });

  const { data: seasonsData } = useQuery({
    queryKey: ['seasons', contentId],
    queryFn: () => apiClient.getSeasons(contentId),
    enabled: isAuthenticated && !!contentData && contentData.content_type === 'series',
  });

  const { data: similarData } = useQuery({
    queryKey: ['similar', contentId],
    queryFn: () => apiClient.getContentList({ page_size: 12 }),
    enabled: isAuthenticated,
  });

  const content = useMemo<Content | null>(() => (contentData ? normalizeContent(contentData) : null), [contentData]);
  const genres = useMemo(() => contentData?.genres?.map((g) => g.name).join(', ') || '—', [contentData]);

  const activeSeason = seasonsData?.[0] ?? null;

  useEffect(() => {
    if (!isAuthenticated) {
      router.push('/login');
      return;
    }
    setInMyList(isInMyList(contentId));
  }, [contentId, isAuthenticated, router]);

  // Start a real playback session against streaming-service.
  useEffect(() => {
    if (!isAuthenticated || !user || !contentId) return;
    if (isStarting === false) return;

    const startSession = async () => {
      try {
        const session = await apiClient.startPlaybackSession({
          user_id: user.id,
          content_id: contentId,
          episode_id: selectedEpisode?.id,
          device_id: 'web-player',
        });
        setSessionId(session.id);
        setIsStarting(false);

        // Resolve a playable URL: prefer a real packaged manifest, else the
        // public HLS test stream (media-pipeline emits stub manifests today).
        if (selectedEpisode) {
          const manifest = await apiClient.getManifestForEpisode(selectedEpisode.id);
          setStreamUrl(manifest?.manifest_url || DEMO_HLS_URL);
        } else {
          setStreamUrl(DEMO_HLS_URL);
        }
      } catch (error) {
        toast.error('Failed to start playback. Please try again.');
        router.push('/browse');
      }
    };

    startSession();
  }, [contentId, selectedEpisode, user, isAuthenticated, router, isStarting]);

  const similarContent: Content[] = useMemo(
    () => (similarData || []).filter((c) => c.id !== contentId).slice(0, 12).map(normalizeContent),
    [similarData, contentId]
  );

  const selectEpisode = (episodeId: string, number: number) => {
    setSelectedEpisode({ id: episodeId, number });
    setSessionId('');
    setIsStarting(true);
  };

  const toggleMyList = () => {
    try {
      const list = JSON.parse(localStorage.getItem(MY_LIST_KEY) || '[]') as string[];
      const idx = list.indexOf(contentId);
      if (idx >= 0) {
        list.splice(idx, 1);
        toast.success('Removed from My List');
      } else {
        list.push(contentId);
        toast.success('Added to My List');
      }
      localStorage.setItem(MY_LIST_KEY, JSON.stringify(list));
      setInMyList(list.includes(contentId));
    } catch {
      toast.error('Could not update My List');
    }
  };

  if (isStarting) {
    return (
      <div className="min-h-screen bg-dark-950 flex items-center justify-center">
        <div className="flex flex-col items-center gap-4">
          <div className="w-10 h-10 border-2 border-red-600 border-t-transparent rounded-full animate-spin" />
          <p className="text-gray-400 text-sm">Starting playback...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-dark-950">
      {/* Video Player */}
      <div className="relative w-full">
        <VideoPlayer contentId={contentId} sessionId={sessionId} src={streamUrl || undefined} />
        {streamUrl === DEMO_HLS_URL && (
          <div className="absolute bottom-16 left-4 bg-black/60 backdrop-blur-sm text-xs text-gray-300 px-3 py-1.5 rounded border border-white/10">
            Preview stream — no packaged media for this title yet.
          </div>
        )}
      </div>

      {/* Content Info */}
      <div className="max-w-6xl mx-auto px-4 sm:px-6 py-6">
        {content && (
          <div className="mb-8">
            <div className="flex items-start justify-between gap-4 mb-4">
              <div>
                <h1 className="text-2xl sm:text-3xl font-bold text-white mb-2">
                  {content.title}
                  {selectedEpisode && content.type === 'show' && (
                    <span className="text-xl text-gray-400"> · Ep {selectedEpisode.number}</span>
                  )}
                </h1>
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
                  {content.duration > 0 && content.type === 'movie' && (
                    <span>{Math.floor(content.duration / 60)}h {content.duration % 60}m</span>
                  )}
                  {content.maturityRating && (
                    <span className="uppercase text-xs font-medium px-2 py-0.5 border border-dark-600 rounded text-gray-400">
                      {content.maturityRating}
                    </span>
                  )}
                  <span className="uppercase text-xs font-medium px-2 py-0.5 border border-dark-600 rounded text-gray-400">
                    {content.type}
                  </span>
                </div>
              </div>
              <div className="flex items-center gap-2 flex-shrink-0">
                <button
                  onClick={toggleMyList}
                  className={`p-2 rounded-full bg-dark-800 hover:bg-dark-700 transition-colors ${inMyList ? 'text-red-500' : 'text-gray-300 hover:text-white'}`}
                  aria-label="Toggle my list"
                >
                  <svg className="w-5 h-5" fill={inMyList ? 'currentColor' : 'none'} viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </div>
            </div>
            <p className="text-gray-400 text-sm leading-relaxed max-w-2xl">
              {content.description}
            </p>
            <div className="mt-3 text-sm text-gray-500">
              <span className="text-gray-400">Genres:</span> {genres}
            </div>
          </div>
        )}

        {/* Episodes / Seasons (for shows) */}
        {contentData?.content_type === 'series' && (
          <div className="mb-8">
            <h2 className="text-lg font-bold text-white mb-4">
              {activeSeason
                ? `Season ${activeSeason.season_number} · ${activeSeason.episode_count} episodes`
                : 'Episodes'}
            </h2>
            <div className="space-y-2">
              {(activeSeason?.episodes?.length ? activeSeason.episodes : []).map((ep) => (
                <button
                  key={ep.id}
                  onClick={() => selectEpisode(ep.id, ep.episode_number)}
                  className="w-full flex items-center gap-4 p-3 rounded-lg hover:bg-dark-800 transition-colors cursor-pointer group text-left"
                >
                  <span className={selectedEpisode?.id === ep.id ? 'text-red-500 w-8 text-center' : 'text-sm text-gray-500 w-8 text-center'}>
                    {ep.episode_number}
                  </span>
                  <div className="w-28 h-16 bg-dark-800 rounded-md overflow-hidden flex-shrink-0">
                    {ep.thumbnail_url ? (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img src={ep.thumbnail_url} alt={ep.title} className="w-full h-full object-cover" />
                    ) : (
                      <div className="w-full h-full bg-dark-700 shimmer" />
                    )}
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm text-white font-medium truncate group-hover:text-red-500 transition-colors">
                      Episode {ep.episode_number}: {ep.title}
                    </p>
                    <p className="text-xs text-gray-500">
                      {ep.duration_minutes}m{ep.release_date ? ` · ${new Date(ep.release_date).getFullYear()}` : ''}
                    </p>
                  </div>
                </button>
              ))}
            </div>
          </div>
        )}

        {/* More Like This */}
        {similarContent.length > 0 && (
          <div className="mb-8">
            <h2 className="text-lg font-bold text-white mb-4">More Like This</h2>
            <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-3">
              {similarContent.map((item: Content) => (
                <MediaCard key={item.id} content={item} />
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