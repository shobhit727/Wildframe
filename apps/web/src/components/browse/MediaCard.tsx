'use client';

import Link from 'next/link';
import { Content } from '@/types';

interface MediaCardProps {
  content: Content;
  variant?: 'poster' | 'backdrop';
  showProgress?: number; // 0-100 watch progress
  /** Show title + year under the card (grid views like My List / search). */
  showCaption?: boolean;
}

export function MediaCard({ content, variant = 'poster', showProgress, showCaption = false }: MediaCardProps) {
  const isPoster = variant === 'poster';

  return (
    <Link href={`/watch/${content.id}`} className="group block">
      <div
        className={`relative overflow-hidden bg-[#1f1f1f] transition-transform duration-300 group-hover:scale-[1.03] group-hover:z-20 ${
          isPoster ? 'aspect-[2/3]' : 'aspect-video'
        }`}
      >
        {/* Placeholder loading surface (no artwork) */}
        <div className="absolute inset-0 shimmer flex items-center justify-center">
          <div className="flex flex-col items-center gap-2">
            <svg className="w-5 h-5 text-gray-700" fill="currentColor" viewBox="0 0 24 24">
              <path d="M8 5v14l11-7z" />
            </svg>
            <span className="uppercase text-[9px] tracking-widest text-gray-700 font-semibold px-3">
              {content.type === 'movie' ? 'Film' : 'Series'}
            </span>
          </div>
        </div>

        {/* Hover overlay with title + play */}
        <div className="absolute inset-0 bg-gradient-to-t from-black/80 via-black/20 to-transparent flex flex-col justify-end p-3 opacity-0 group-hover:opacity-100 transition-opacity duration-200">
          <h3 className="text-sm font-semibold text-white mb-2 truncate">
            {content.title}
          </h3>
          <div className="flex items-center gap-2">
            <span className="text-[#46d369] text-xs font-semibold">
              {Math.min(99, Math.max(75, content.rating * 10))}% Match
            </span>
            {content.rating > 0 && (
              <span className="text-[11px] text-gray-400">{content.rating.toFixed(1)}</span>
            )}
          </div>
          <div className="flex items-center gap-2 mt-2">
            <span className="border border-white/60 px-1.5 py-0.5 text-[10px] uppercase text-gray-200">
              {content.type}
            </span>
          </div>
        </div>

        {/* Watch Progress Bar */}
        {showProgress !== undefined && showProgress > 0 && (
          <div className="absolute bottom-0 left-0 right-0 h-[3px] bg-black/60">
            <div
              className="h-full bg-[#E50914]"
              style={{ width: `${Math.min(showProgress, 100)}%` }}
            />
          </div>
        )}
      </div>

      {showCaption && (
        <div className="mt-2 space-y-0.5">
          <h3 className="text-sm text-gray-200 group-hover:text-white transition-colors truncate">
            {content.title}
          </h3>
          <p className="text-xs text-gray-500">
            {content.releaseDate?.split('-')[0]}
            {content.rating > 0 ? ` · ★ ${content.rating.toFixed(1)}` : ''}
          </p>
        </div>
      )}
    </Link>
  );
}