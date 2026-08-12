'use client';

import { useEffect, useRef, useState, useCallback } from 'react';
import { apiClient } from '@/api/client';

interface VideoPlayerProps {
  contentId: string;
  sessionId: string;
  src?: string;
  srcType?: 'hls' | 'dash' | 'mp4';
  onEnded?: () => void;
}

export function VideoPlayer({ contentId, sessionId, src, srcType = 'hls', onEnded }: VideoPlayerProps) {
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
  const [loadError, setLoadError] = useState(false);
  const [loadAttempt, setLoadAttempt] = useState(0);

  const hideControlsDelayed = useCallback(() => {
    if (!isPlaying) return;
    if (controlsTimeoutRef.current) clearTimeout(controlsTimeoutRef.current);
    controlsTimeoutRef.current = setTimeout(() => setShowControls(false), 2500);
  }, [isPlaying]);

  const handleMouseMove = () => {
    setShowControls(true);
    hideControlsDelayed();
  };

  const handleRetry = () => {
    setLoadError(false);
    setLoadAttempt((c) => c + 1);
  };

  useEffect(() => {
    let cancelled = false;

    const initPlayer = async () => {
      try {
        let url = src;
        let type = srcType;
        if (!url) {
          const response = await apiClient.getManifestForEpisode(contentId);
          if (response?.manifest_url) {
            url = response.manifest_url;
            type = response.protocol === 'dash' ? 'dash' : 'hls';
          }
        }
        if (cancelled || !url || !videoRef.current) return;

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

            // Fatal HLS errors -> show retry overlay
            hls.on(Hls.Events.ERROR, (_e, data) => {
              if (cancelled) return;
              if (data.fatal) {
                console.error('HLS fatal error:', data);
                setLoadError(true);
              }
            });
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
        if (!cancelled) {
          console.error('Failed to load video:', error);
          setLoadError(true);
        }
      }
    };

    initPlayer();

    return () => {
      cancelled = true;
      if (controlsTimeoutRef.current) clearTimeout(controlsTimeoutRef.current);
      const player = playerRef.current;
      if (player && typeof (player as { destroy?: () => void }).destroy === 'function') {
        (player as { destroy: () => void }).destroy();
      }
      playerRef.current = null;
    };
  }, [contentId, quality, src, srcType, loadAttempt]);

  const handleTimeUpdate = () => {
    if (!videoRef.current) return;
    const t = videoRef.current.currentTime;
    setCurrentTime(t);

    if (Math.floor(t) % 30 === 0 && Math.floor(t) > 0) {
      apiClient.updatePlaybackPosition(sessionId, t).catch(() => {});
    }
  };

  const handleEnded = async () => {
    setIsPlaying(false);
    await apiClient.endPlaybackSession(sessionId).catch(() => {});
    onEnded?.();
  };

  const togglePlay = () => {
    if (!videoRef.current) return;
    if (isPlaying) {
      videoRef.current.pause();
    } else {
      videoRef.current.play().catch(() => {});
    }
    setIsPlaying(!isPlaying);
  };

  const toggleMute = () => {
    if (!videoRef.current) return;
    videoRef.current.muted = !isMuted;
    setIsMuted(!isMuted);
  };

  const handleVolumeChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (!videoRef.current) return;
    const v = Number(e.target.value);
    videoRef.current.volume = v;
    setVolume(v);
    setIsMuted(v === 0);
  };

  const toggleFullscreen = async () => {
    if (!videoRef.current) return;
    if (!isFullscreen) {
      try {
        await videoRef.current.requestFullscreen();
      } catch {
        // ignore
      }
    } else {
      try {
        await document.exitFullscreen();
      } catch {
        // ignore
      }
    }
    setIsFullscreen(!isFullscreen);
  };

  const seek = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!videoRef.current) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const percentage = (e.clientX - rect.left) / rect.width;
    videoRef.current.currentTime = percentage * duration;
  };

  const formatTime = (s: number) => {
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = Math.floor(s % 60);
    return h > 0 ? `${h}:${m.toString().padStart(2, '0')}:${sec.toString().padStart(2, '0')}` : `${m}:${sec.toString().padStart(2, '0')}`;
  };

  const progress = duration > 0 ? (currentTime / duration) * 100 : 0;

  return (
    <div
      className="relative w-full aspect-video bg-black"
      onMouseMove={handleMouseMove}
      onMouseLeave={() => {
        if (controlsTimeoutRef.current) clearTimeout(controlsTimeoutRef.current);
        controlsTimeoutRef.current = setTimeout(() => setShowControls(false), 1500);
      }}
    >
      <video
        ref={videoRef}
        className="w-full h-full object-contain"
        onLoadedMetadata={() => videoRef.current && setDuration(videoRef.current.duration)}
        onTimeUpdate={handleTimeUpdate}
        onEnded={handleEnded}
        onPlay={() => setIsPlaying(true)}
        onPause={() => setIsPlaying(false)}
        onError={() => {
          if (!cancelled) setLoadError(true);
        }}
        playsInline
      />
      {loadError && (
        <div
          className="absolute inset-0 flex flex-col items-center justify-center bg-black/80 z-10"
          role="alert"
          aria-live="assertive"
        >
          <p className="text-white text-lg mb-4">Unable to play video</p>
          <button
            onClick={handleRetry}
            className="px-6 py-3 bg-white text-black rounded font-medium hover:bg-gray-200 transition-colors focus:outline-none focus:ring-2 focus:ring-white"
          >
            Retry
          </button>
        </div>
      )}
      {showControls && (
        <div className="absolute bottom-0 left-0 right-0 p-4 bg-gradient-to-t from-black/90 to-transparent transition-opacity duration-300">
          <div className="mb-2 h-1 bg-gray-700 rounded-full cursor-pointer relative" onClick={seek}>
            <div
              className="h-full bg-white rounded-full"
              style={{ width: `${progress}%` }}
              role="slider"
              aria-label="Playback progress"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={Math.round(progress)}
            />
          </div>
          <div className="flex items-center justify-between gap-4">
            <div className="flex items-center gap-2">
              <button
                onClick={togglePlay}
                className="p-2 text-white hover:text-gray-300 transition-colors focus:outline-none focus:ring-2 focus:ring-white rounded"
                aria-label={isPlaying ? 'Pause' : 'Play'}
              >
                {isPlaying ? (
                  <svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                    <path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z" />
                  </svg>
                ) : (
                  <svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                    <path d="M8 5v14l11-7z" />
                  </svg>
                )}
              </button>
              <span className="text-white text-sm font-mono">
                {formatTime(currentTime)} / {formatTime(duration)}
              </span>
            </div>
            <div className="flex items-center gap-2 ml-auto">
              <label htmlFor="volume" className="sr-only">Volume</label>
              <input
                id="volume"
                type="range"
                min={0}
                max={1}
                step={0.1}
                value={isMuted ? 0 : volume}
                onChange={handleVolumeChange}
                className="w-24 h-2 accent-white"
                aria-label="Volume"
              />
              <button
                onClick={toggleMute}
                className="p-2 text-white hover:text-gray-300 transition-colors focus:outline-none focus:ring-2 focus:ring-white rounded"
                aria-label={isMuted ? 'Unmute' : 'Mute'}
              >
                {isMuted || volume === 0 ? (
                  <svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                    <path d="M16.5 12c0-1.77-1.02-3.29-2.5-4.03v2.21l2.45 2.45c.03-.2.05-.41.05-.63zm2.5 0c0 .94-.2 1.82-.54 2.64l1.51 1.51C20.63 14.91 21 13.5 21 12c0-4.28-2.99-7.86-7-8.77v2.06c2.89.86 5 3.54 5 6.71zM4.27 3L3 4.27 7.73 9H3v6h4l5 5v-6.73l4.25 4.25c-.67.52-1.42.93-2.25 1.18v2.06c1.38-.31 2.63-.95 3.69-1.81L19.73 21 21 19.73l-9-9L4.27 3zM12 4L9.91 6.09 12 8.18V4z" />
                  </svg>
                ) : (
                  <svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                    <path d="M3 9v6h4l5 5V4L7 9H3zm13.5 3c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02zM14 3.23v2.06c2.89.86 5 3.54 5 6.71s-2.11 5.85-5 6.71v2.06c4.01-.91 7-4.49 7-8.77s-2.99-7.86-7-8.77z" />
                  </svg>
                )}
              </button>
              <label htmlFor="quality" className="sr-only">Video quality</label>
              <select
                id="quality"
                value={quality}
                onChange={(e) => setQuality(e.target.value)}
                className="bg-gray-800 text-white px-3 py-1.5 rounded border border-gray-600 text-sm focus:outline-none focus:ring-2 focus:ring-white"
                aria-label="Video quality"
              >
                <option value="auto">Auto</option>
                <option value="1080p">1080p</option>
                <option value="720p">720p</option>
                <option value="480p">480p</option>
              </select>
              <button
                onClick={toggleFullscreen}
                className="p-2 text-white hover:text-gray-300 transition-colors focus:outline-none focus:ring-2 focus:ring-white rounded"
                aria-label={isFullscreen ? 'Exit fullscreen' : 'Enter fullscreen'}
              >
                {isFullscreen ? (
                  <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                    <path d="M8 16H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2" />
                    <path d="M16 8h2a2 2 0 0 1 2 2v2" />
                    <path d="M12 12v-2h2" />
                  </svg>
                ) : (
                  <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                    <path d="M8 3H5a2 2 0 0 0-2 2v3" />
                    <path d="M21 8V5a2 2 0 0 0-2-2h-3" />
                    <path d="M3 16v3a2 2 0 0 0 2 2h3" />
                    <path d="M16 21h3a2 2 0 0 0 2-2v-3" />
                  </svg>
                )}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}