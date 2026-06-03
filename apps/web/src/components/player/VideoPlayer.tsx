'use client';

import { useEffect, useRef, useState } from 'react';
import Hls from 'hls.js';
import { apiClient } from '@/api/client';

interface VideoPlayerProps {
  contentId: string;
  sessionId: string;
}

export function VideoPlayer({ contentId, sessionId }: VideoPlayerProps) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [isPlaying, setIsPlaying] = useState(false);
  const [duration, setDuration] = useState(0);
  const [currentTime, setCurrentTime] = useState(0);
  const [quality, setQuality] = useState('auto');

  useEffect(() => {
    const initPlayer = async () => {
      try {
        const response = await apiClient.getManifest(contentId, quality);
        const { url, type } = response.data;

        if (!videoRef.current) return;

        if (type === 'hls' && Hls.isSupported()) {
          const hls = new Hls();
          hls.loadSource(url);
          hls.attachMedia(videoRef.current);
        } else {
          videoRef.current.src = url;
        }
      } catch (error) {
        console.error('Failed to load video:', error);
      }
    };

    initPlayer();
  }, [contentId, quality]);

  const handleTimeUpdate = async () => {
    if (!videoRef.current) return;
    setCurrentTime(videoRef.current.currentTime);

    // Save position every 30 seconds
    if (Math.floor(videoRef.current.currentTime) % 30 === 0) {
      try {
        await apiClient.updateWatchPosition(sessionId, videoRef.current.currentTime);
      } catch (error) {
        console.error('Failed to save position:', error);
      }
    }
  };

  const handleEnded = async () => {
    try {
      await apiClient.endSession(sessionId);
    } catch (error) {
      console.error('Failed to end session:', error);
    }
  };

  return (
    <div className="w-full bg-black">
      <video
        ref={videoRef}
        controls
        onTimeUpdate={handleTimeUpdate}
        onEnded={handleEnded}
        onPlay={() => setIsPlaying(true)}
        onPause={() => setIsPlaying(false)}
        onLoadedMetadata={(e) => setDuration(e.currentTarget.duration)}
        className="w-full"
      />
      
      <div className="p-4 bg-gray-900">
        <div className="flex items-center justify-between mb-4">
          <span className="text-white">
            {Math.floor(currentTime / 60)}:{String(Math.floor(currentTime % 60)).padStart(2, '0')} / {Math.floor(duration / 60)}:{String(Math.floor(duration % 60)).padStart(2, '0')}
          </span>
          <select
            value={quality}
            onChange={(e) => setQuality(e.target.value)}
            className="bg-gray-800 text-white px-3 py-2 rounded"
          >
            <option value="auto">Auto</option>
            <option value="1080p">1080p</option>
            <option value="720p">720p</option>
            <option value="480p">480p</option>
          </select>
        </div>
      </div>
    </div>
  );
}
