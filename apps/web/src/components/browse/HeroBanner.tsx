'use client';

import Image from 'next/image';
import Link from 'next/link';
import { Content } from '@/types';
import { useState } from 'react';

interface HeroBannerProps {
  items: Content[];
}

export function HeroBanner({ items }: HeroBannerProps) {
  const [activeIndex, setActiveIndex] = useState(0);
  const [imageError, setImageError] = useState(false);

  if (items.length === 0) return null;

  const active = items[activeIndex];
  if (!active) return null;

  return (
    <section className="relative w-full h-[70vh] min-h-[480px] max-h-[800px]">
      {/* Backdrop Image */}
      {!imageError ? (
        <Image
          src={active.backdrop}
          alt={active.title}
          fill
          priority
          sizes="100vw"
          className="object-cover object-center"
          onError={() => setImageError(true)}
        />
      ) : (
        <div className="absolute inset-0 bg-gradient-to-br from-dark-800 via-dark-900 to-dark-950" />
      )}

      {/* Gradient Overlays */}
      <div className="hero-gradient absolute inset-0" />
      <div className="hero-gradient-left absolute inset-0" />

      {/* Content */}
      <div className="absolute bottom-[15%] left-0 right-0 px-4 sm:px-6 lg:px-8 max-w-[1440px] mx-auto">
        <div className="max-w-xl animate-fade-in" key={active.id}>
          {/* Type Badge */}
          <span className="inline-block text-xs font-semibold uppercase tracking-wider text-red-500 mb-3">
            {active.type === 'movie' ? 'Movie' : 'TV Series'}
          </span>

          {/* Title */}
          <h1 className="text-3xl sm:text-4xl lg:text-5xl font-bold text-white leading-tight mb-3">
            {active.title}
          </h1>

          {/* Meta */}
          <div className="flex items-center gap-3 text-sm text-gray-300 mb-4">
            {active.rating > 0 && (
              <span className="flex items-center gap-1">
                <svg className="w-4 h-4 text-yellow-500" fill="currentColor" viewBox="0 0 20 20">
                  <path d="M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.07 3.292a1 1 0 00.95.69h3.462c.969 0 1.371 1.24.588 1.81l-2.8 2.034a1 1 0 00-.364 1.118l1.07 3.292c.3.921-.755 1.688-1.54 1.118l-2.8-2.034a1 1 0 00-1.175 0l-2.8 2.034c-.784.57-1.838-.197-1.539-1.118l1.07-3.292a1 1 0 00-.364-1.118L2.98 8.72c-.783-.57-.38-1.81.588-1.81h3.461a1 1 0 00.951-.69l1.07-3.292z" />
                </svg>
                {active.rating.toFixed(1)}
              </span>
            )}
            <span>{active.releaseDate?.split('-')[0]}</span>
            {active.duration > 0 && (
              <span className="flex items-center gap-1">
                <span className="opacity-50">|</span>
                {Math.floor(active.duration / 60)}h {active.duration % 60}m
              </span>
            )}
            <span className="uppercase text-xs font-medium px-2 py-0.5 border border-gray-500 rounded text-gray-400">
              {active.genre}
            </span>
          </div>

          {/* Description */}
          <p className="text-sm text-gray-300 leading-relaxed mb-6 line-clamp-3">
            {active.description}
          </p>

          {/* CTA Buttons */}
          <div className="flex items-center gap-3">
            <Link
              href={`/watch/${active.id}`}
              className="inline-flex items-center gap-2 bg-white hover:bg-gray-200 text-black font-semibold px-6 py-2.5 rounded-md transition-colors"
            >
              <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
                <path d="M8 5v14l11-7z" />
              </svg>
              Play
            </Link>
            <button
              className="inline-flex items-center gap-2 bg-gray-500/30 hover:bg-gray-500/50 text-white font-semibold px-6 py-2.5 rounded-md backdrop-blur-sm transition-colors border border-gray-500/30"
            >
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 4.5v15m7.5-7.5h-15" />
              </svg>
              My List
            </button>
            <button
              className="p-2.5 rounded-full bg-gray-500/30 hover:bg-gray-500/50 text-white backdrop-blur-sm transition-colors border border-gray-500/30"
              aria-label="More info"
            >
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008z" />
              </svg>
            </button>
          </div>
        </div>
      </div>

      {/* Dot Indicators */}
      {items.length > 1 && (
        <div className="absolute bottom-6 left-1/2 -translate-x-1/2 flex items-center gap-2">
          {items.slice(0, 5).map((_, i) => (
            <button
              key={i}
              onClick={() => {
                setActiveIndex(i);
                setImageError(false);
              }}
              className={`h-1.5 rounded-full transition-all duration-300 ${
                i === activeIndex
                  ? 'w-8 bg-white'
                  : 'w-1.5 bg-white/30 hover:bg-white/60'
              }`}
              aria-label={`Go to slide ${i + 1}`}
            />
          ))}
        </div>
      )}
    </section>
  );
}
