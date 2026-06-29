'use client';

import Image from 'next/image';
import Link from 'next/link';
import { Content } from '@/types';
import { useState } from 'react';

interface MediaCardProps {
  content: Content;
  variant?: 'poster' | 'backdrop';
  showProgress?: number; // 0-100 watch progress
}

export function MediaCard({ content, variant = 'poster', showProgress }: MediaCardProps) {
  const [imageError, setImageError] = useState(false);
  const isPoster = variant === 'poster';

  return (
    <Link href={`/watch/${content.id}`} className="group block">
      <div
        className={`relative overflow-hidden rounded-md bg-dark-800 ${
          isPoster ? 'aspect-[2/3]' : 'aspect-video'
        }`}
      >
        {/* Image */}
        {!imageError ? (
          <Image
            src={isPoster ? content.poster : content.backdrop}
            alt={content.title}
            fill
            sizes={isPoster ? '(max-width: 640px) 50vw, (max-width: 1024px) 33vw, 16vw' : '(max-width: 768px) 100vw, 50vw'}
            className="object-cover transition-transform duration-300 group-hover:scale-110"
            onError={() => setImageError(true)}
          />
        ) : (
          <div className="absolute inset-0 flex items-center justify-center bg-dark-800">
            <span className="text-4xl opacity-30">
              {content.type === 'movie' ? '🎬' : '📺'}
            </span>
          </div>
        )}

        {/* Hover Overlay */}
        <div className="absolute inset-0 bg-black/70 opacity-0 group-hover:opacity-100 transition-opacity duration-200 flex items-center justify-center">
          <div className="w-12 h-12 rounded-full bg-white/20 backdrop-blur-sm flex items-center justify-center border border-white/30">
            <svg className="w-5 h-5 text-white ml-0.5" fill="currentColor" viewBox="0 0 24 24">
              <path d="M8 5v14l11-7z" />
            </svg>
          </div>
        </div>

        {/* Watch Progress Bar */}
        {showProgress !== undefined && showProgress > 0 && (
          <div className="absolute bottom-0 left-0 right-0 h-1 bg-dark-900/50">
            <div
              className="h-full bg-red-600 rounded-r-full"
              style={{ width: `${Math.min(showProgress, 100)}%` }}
            />
          </div>
        )}

        {/* Rating Badge */}
        {content.rating > 0 && (
          <div className="absolute top-2 right-2 bg-black/60 backdrop-blur-sm text-white text-xs font-medium px-1.5 py-0.5 rounded">
            {content.rating.toFixed(1)}
          </div>
        )}
      </div>

      {/* Title & Meta */}
      <div className="mt-2 space-y-0.5">
        <h3 className="text-sm font-medium text-gray-200 group-hover:text-white transition-colors truncate">
          {content.title}
        </h3>
        <div className="flex items-center gap-1.5 text-xs text-gray-500">
          <span>{content.releaseDate?.split('-')[0]}</span>
          {content.duration > 0 && (
            <>
              <span className="opacity-40">&middot;</span>
              <span>{Math.floor(content.duration / 60)}h {content.duration % 60}m</span>
            </>
          )}
          <span className="opacity-40">&middot;</span>
          <span className="uppercase text-[10px] font-medium px-1 py-0.5 border border-dark-600 rounded text-gray-400">
            {content.type}
          </span>
        </div>
      </div>
    </Link>
  );
}
