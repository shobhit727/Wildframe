'use client';

import { useEffect, useRef, useState, useCallback } from 'react';
import { apiClient } from '@/api/client';

interface VideoPlayerProps {
  contentId: string;
  sessionId: string;
  onEnded?: () => void;
}

export function VideoPlayer({ contentId, sessionId, onEnded }: VideoPlayerProps) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const playerRef = useRef<unknown>(null);
  const [isPlaying, setIsPlaying] = useState(false);
  const [duration, setDuration] = useState(0);
  const [currentTime, setCurrentTime] = useState(0);
  const [volume, setVolume] = useState(1);
  const [isMuted, setIsMuted] = useState(false);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [quality, setQuality] = useState('auto');
  const [showControls, setShowControls] = useState(true);
  const controlsTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const hideControlsDelayed = useCallback(() => {
    if (controlsTimeoutRef.current) clearTimeout(controlsTimeoutRef.current);
    controlsTimeoutRef.current = setTimeout(() => {
      if (isPlaying) setShowControls(false);
    }, 3000);
  }, [isPlaying]);

  const handleMouseMove = () => {
    setShowControls(true);
    hideControlsDelayed();
  };

  useEffect(() => {
    const initPlayer = async () => {
      try {
        const response = await apiClient.getManifest(contentId, quality);
        const { url, type } = response.data;

        if (!videoRef.current) return;

        if (type === 'hls') {
          // Dynamic import to avoid SSR issues
          const Hls = (await import('hls.js')).default;
          if (Hls.isSupported()) {
            const hls = new Hls({
              enableWorker: true,
              lowLatencyMode: false,
            });
            hls.loadSource(url);
            hls.attachMedia(videoRef.current);
            playerRef.current = hls;
          } else if (videoRef.current.canPlayType('application/vnd.apple.mpegurl')) {
            videoRef.current.src = url;
          }
        } else if (type === 'dash') {
          const dashjs = await import('dashjs');
          const player = dashjs.MediaPlayer().create();
          player.initialize(videoRef.current, url, false);
          playerRef.current = player;
        } else {
          videoRef.current.src = url;
        }
      } catch (error) {
        console.error('Failed to load video:', error);
      }
    };

    initPlayer();

    return () => {
      // Cleanup
      const player = playerRef.current;
      if (player && typeof (player as { destroy?: () => void }).destroy === 'function') {
        (player as { destroy: () => void }).destroy();
      }
      playerRef.current = null;
    };
  }, [contentId, quality]);

  const handleTimeUpdate = () => {
    if (!videoRef.current) return;
    const t = videoRef.current.currentTime;
    setCurrentTime(t);

    // Save position every 30 seconds
    if (Math.floor(t) % 30 === 0 && Math.floor(t) > 0) {
      apiClient.updateWatchPosition(sessionId, t).catch(() => {});
    }
  };

  const handleEnded = async () => {
    setIsPlaying(false);
    try {
      await apiClient.endSession(sessionId);
    } catch {}
    onEnded?.();
  };

  const togglePlay = () => {
    if (!videoRef.current) return;
    if (isPlaying) {
      videoRef.current.pause();
    } else {
      videoRef.current.play();
    }
  };

  const toggleMute = () => {
    if (!videoRef.current) return;
    videoRef.current.muted = !isMuted;
    setIsMuted(!isMuted);
  };

  const handleVolumeChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const v = parseFloat(e.target.value);
    setVolume(v);
    if (videoRef.current) videoRef.current.volume = v;
  };

  const toggleFullscreen = async () => {
    const container = videoRef.current?.parentElement;
    if (!container) return;
    try {
      if (!document.fullscreenElement) {
        await container.requestFullscreen();
        setIsFullscreen(true);
      } else {
        await document.exitFullscreen();
        setIsFullscreen(false);
      }
    } catch {}
  };

  const seek = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!videoRef.current || duration === 0) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const pos = (e.clientX - rect.left) / rect.width;
    videoRef.current.currentTime = pos * duration;
  };

  const formatTime = (s: number) => {
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = Math.floor(s % 60);
    if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`;
    return `${m}:${String(sec).padStart(2, '0')}`;
  };

  const progress = duration > 0 ? (currentTime / duration) * 100 : 0;

  return (
    <div
      className="relative w-full bg-black group/player"
      onMouseMove={handleMouseMove}
      onMouseLeave={() => isPlaying && setShowControls(false)}
    >
      {/* Video Element */}
      <video
        ref={videoRef}
        className="w-full aspect-video bg-black"
        onTimeUpdate={handleTimeUpdate}
        onEnded={handleEnded}
        onPlay={() => setIsPlaying(true)}
        onPause={() => setIsPlaying(false)}
        onLoadedMetadata={(e) => setDuration(e.currentTarget.duration)}
        onClick={togglePlay}
      />

      {/* Big Center Play Button (when paused) */}
      {!isPlaying && (
        <button
          onClick={togglePlay}
          className="absolute inset-0 flex items-center justify-center z-10"
        >
          <div className="w-20 h-20 rounded-full bg-black/40 backdrop-blur-sm flex items-center justify-center border border-white/20 hover:bg-black/60 transition-colors">
            <svg className="w-10 h-10 text-white ml-1" fill="currentColor" viewBox="0 0 24 24">
              <path d="M8 5v14l11-7z" />
            </svg>
          </div>
        </button>
      )}

      {/* Controls Overlay */}
      <div
        className={`absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black/90 to-transparent pt-16 pb-3 px-4 transition-opacity duration-300 ${
          showControls ? 'opacity-100' : 'opacity-0'
        }`}
      >
        {/* Progress Bar */}
        <div className="group/progress mb-3 cursor-pointer" onClick={seek}>
          <div className="h-1 group-hover/progress:h-2 bg-white/20 rounded-full transition-all relative">
            <div
              className="h-full bg-red-600 rounded-full relative"
              style={{ width: `${progress}%` }}
            >
              <div className="absolute right-0 top-1/2 -translate-y-1/2 w-3 h-3 bg-red-600 rounded-full opacity-0 group-hover/progress:opacity-100 transition-opacity shadow-lg" />
            </div>
          </div>
        </div>

        {/* Controls Row */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            {/* Play/Pause */}
            <button onClick={togglePlay} className="text-white hover:text-gray-300 transition-colors" aria-label={isPlaying ? 'Pause' : 'Play'}>
              {isPlaying ? (
                <svg className="w-6 h-6" fill="currentColor" viewBox="0 0 24 24"><path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z" /></svg>
              ) : (
                <svg className="w-6 h-6" fill="currentColor" viewBox="0 0 24 24"><path d="M8 5v14l11-7z" /></svg>
              )}
            </button>

            {/* Skip Back */}
            <button onClick={() => { if (videoRef.current) videoRef.current.currentTime -= 10; }} className="text-white hover:text-gray-300 transition-colors" aria-label="Skip back 10s">
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" d="M9 15L3 9m0 0l6-6M3 9h12a6 6 0 010 12h-3" /></svg>
            </button>

            {/* Skip Forward */}
            <button onClick={() => { if (videoRef.current) videoRef.current.currentTime += 10; }} className="text-white hover:text-gray-300 transition-colors" aria-label="Skip forward 10s">
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" d="M15 15l6-6m0 0l-6-6m6 6H9a6 6 0 000 12h3" /></svg>
            </button>

            {/* Volume */}
            <div className="flex items-center gap-2 group/vol">
              <button onClick={toggleMute} className="text-white hover:text-gray-300 transition-colors" aria-label={isMuted ? 'Unmute' : 'Mute'}>
                {isMuted || volume === 0 ? (
                  <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" d="M17.25 9.25L19.5 7m0 0L21 5.25m-1.5 1.75L17.25 9.25M3 9.75l4.5 3V18l4.5-3 4.5 3V9.75" /></svg>
                ) : (
                  <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" d="M3 9.75l4.5 3V18l4.5-3 4.5 3V9.75M15.75 15a3.75 3.75 0 01-7.5 0" /></svg>
                )}
              </button>
              <input
                type="range"
                min={0}
                max={1}
                step={0.05}
                value={isMuted ? 0 : volume}
                onChange={handleVolumeChange}
                className="w-0 group-hover/vol:w-20 transition-all duration-200 accent-red-600 h-1 cursor-pointer"
              />
            </div>

            {/* Time */}
            <span className="text-sm text-gray-300 font-mono">
              {formatTime(currentTime)} / {formatTime(duration)}
            </span>
          </div>

          <div className="flex items-center gap-3">
            {/* Quality Selector */}
            <select
              value={quality}
              onChange={(e) => setQuality(e.target.value)}
              className="bg-transparent text-white text-xs border border-white/20 rounded px-2 py-1 cursor-pointer hover:border-white/40 transition-colors"
            >
              <option value="auto" className="bg-dark-900">Auto</option>
              <option value="1080p" className="bg-dark-900">1080p</option>
              <option value="720p" className="bg-dark-900">720p</option>
              <option value="480p" className="bg-dark-900">480p</option>
            </select>

            {/* Fullscreen */}
            <button onClick={toggleFullscreen} className="text-white hover:text-gray-300 transition-colors" aria-label="Fullscreen">
              {isFullscreen ? (
                <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" d="M9 9V4.5M9 9H4.5M9 9L3.75 3.75M9 15v4.5M9 15H4.5M9 15l-5.25 5.25M15 9h4.5M15 9V4.5M15 9l5.25-5.25M15 15h4.5M15 15v4.5m0-4.5l5.25 5.25" /></svg>
              ) : (
                <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" d="M3.75 3.75v4.5m0-4.5h4.5m-4.5 0L9 9M3.75 20.25v-4.5m0 4.5h4.5m-4.5 0L9 15M20.25 3.75h-4.5m4.5 0v4.5m0-4.5L15 9m5.25 11.25h-4.5m4.5 0v-4.5m0 4.5L15 15" /></svg>
              )}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
