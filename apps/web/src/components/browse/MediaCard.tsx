'use client';

import Link from 'next/link';
import { Content } from '@/types';
import { PosterArt } from '@/components/common/PosterArt';

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
    <Link href={`/watch/${content.id}`} className="group block rounded-md focus-visible:ring-2 focus-visible:ring-[#e50914]">
      <div
        className={`wf-lift relative overflow-hidden rounded-md bg-[#141414] ring-1 ring-white/10 transition-shadow duration-200 group-hover:shadow-[0_18px_50px_rgba(0,0,0,0.55)] group-hover:ring-white/25 ${
          isPoster ? 'aspect-[2/3]' : 'aspect-video'
        }`}
      >
        {/* Generated artwork: deterministic per title, no remote assets needed. */}
        <div className="absolute inset-0 transition-transform duration-300 ease-out group-hover:scale-[1.04]">
          <PosterArt seed={`${content.id}:${content.title}`} title={content.title} variant={variant} />
        </div>

        {/* Hover overlay gives desktop users quick metadata without covering the artwork at rest. */}
        <div className="absolute inset-0 flex flex-col justify-end bg-gradient-to-t from-black/90 via-black/20 to-transparent p-3 opacity-0 transition-opacity duration-200 group-hover:opacity-100 group-focus-visible:opacity-100">
          <h3 className="mb-2 truncate text-sm font-semibold text-white">
            {content.title}
          </h3>
          <div className="flex items-center gap-2">
            <span className="text-xs font-semibold text-[#46d369]">
              {Math.min(99, Math.max(75, content.rating * 10))}% Match
            </span>
            {content.rating > 0 && (
              <span className="text-[11px] text-gray-400">{content.rating.toFixed(1)}</span>
            )}
          </div>
          <div className="mt-2 flex items-center gap-2">
            <span className="border border-white/60 px-1.5 py-0.5 text-[10px] uppercase text-gray-200">
              {content.type}
            </span>
          </div>
        </div>

        {/* Type badge stays visible at rest so rows scan quickly. */}
        <span className="absolute right-2 top-2 rounded bg-black/55 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-widest text-white/80 backdrop-blur-sm">
          {content.type === 'movie' ? 'Film' : 'Series'}
        </span>

        {/* Progress remains visible even when the card is not hovered. */}
        {showProgress !== undefined && showProgress > 0 && (
          <div className="absolute bottom-0 left-0 right-0 h-[3px] bg-black/60">
            <div
              className="h-full bg-[#E50914] transition-[width] duration-500"
              style={{ width: `${Math.min(showProgress, 100)}%` }}
            />
          </div>
        )}
      </div>

      {showCaption && (
        <div className="mt-2 space-y-0.5">
          <h3 className="truncate text-sm text-gray-200 transition-colors group-hover:text-white">
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
