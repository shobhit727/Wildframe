'use client';

import { useEffect, useState } from 'react';
import { useParams } from 'next/navigation';
import { VideoPlayer } from '@/components/player/VideoPlayer';
import { apiClient } from '@/api/client';
import { useIsAuthenticated } from '@/hooks';
import { useRouter } from 'next/navigation';

export default function WatchPage() {
  const params = useParams();
  const contentId = params.id as string;
  const isAuthenticated = useIsAuthenticated();
  const router = useRouter();
  const [sessionId, setSessionId] = useState<string>('');
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    if (!isAuthenticated) {
      router.push('/login');
      return;
    }

    const startSession = async () => {
      try {
        const response = await apiClient.startStreaming(contentId, 'web-player');
        setSessionId(response.data.session_id);
        setIsLoading(false);
      } catch (error) {
        console.error('Failed to start streaming:', error);
        router.push('/browse');
      }
    };

    startSession();
  }, [contentId, isAuthenticated, router]);

  if (isLoading) {
    return (
      <div className="w-full h-screen bg-black flex items-center justify-center">
        <div className="text-white text-2xl">Loading player...</div>
      </div>
    );
  }

  return (
    <div className="w-full bg-black">
      <VideoPlayer contentId={contentId} sessionId={sessionId} />
    </div>
  );
}
