'use client';

import Link from 'next/link';
import { Content } from '@/types';
import { useState, useEffect } from 'react';
import { PosterArt } from '@/components/common/PosterArt';

interface HeroBannerProps {
  items: Content[];
}

export function HeroBanner({ items }: HeroBannerProps) {
  const [activeIndex, setActiveIndex] = useState(0);

  useEffect(() => {
    if (items.length <= 1) return;
    const id = setInterval(() => {
      setActiveIndex((i) => (i + 1) % items.length);
    }, 12000);
    return () => clearInterval(id);
  }, [items.length]);

  if (items.length === 0) return null;

  const active = items[activeIndex];
  if (!active) return null;

  const match = active.rating > 0 ? Math.min(99, Math.max(75, active.rating * 10)) : 97;

  return (
    <section className="relative w-full h-[56vw] max-h-[760px] min-h-[380px] bg-black">
      {/* Generated backdrop art, cross-fading between featured titles */}
      {items.slice(0, 5).map((item, i) => (
        <div
          key={item.id}
          className={`absolute inset-0 transition-opacity duration-1000 ${
            i === activeIndex ? 'opacity-100' : 'opacity-0'
          }`}
        >
          <PosterArt seed={`${item.id}:${item.title}`} title={item.title} variant="backdrop" />
        </div>
      ))}

      {/* Netflix billboard gradients */}
      <div className="absolute inset-0 billboard-gradient" />
      <div className="absolute inset-0 billboard-gradient-left" />

      {/* Top fade for navbar readability */}
      <div className="absolute top-0 left-0 right-0 h-24 bg-gradient-to-b from-black/60 to-transparent" />

      {/* Content */}
      <div className="absolute bottom-[10%] left-0 right-0 px-8 max-w-3xl z-10">
        <div className="animate-fade-in" key={active.id}>
          {/* Type logo-ish line */}
          <span className="block text-xl font-medium text-white/90 mb-4 select-none">
            WILDFRAME&nbsp;
            <span className="text-[#E50914]">
              {active.type === 'movie' ? 'ORIGINAL FILM' : 'ORIGINAL SERIES'}
            </span>
          </span>

          {/* Title */}
          <h1 className="text-4xl sm:text-5xl lg:text-7xl font-bold text-white leading-none mb-5">
            {active.title}
          </h1>

          {/* Meta: match, tonen, age, hd */}
          <div className="flex items-center gap-2 sm:gap-3 text-[11px] sm:text-sm mb-4">
            <span className="text-[#46d369] font-semibold">{match}% Match</span>
            <span className="text-gray-300">{active.releaseDate?.split('-')[0]}</span>
            {active.duration > 0 && (
              <span className="text-gray-300">
                {Math.floor(active.duration / 60)}h {active.duration % 60}m
              </span>
            )}
            <span className="hidden sm:inline border border-gray-500 px-1.5 py-0.5 text-gray-300">
              {active.maturityRating || 'TV-MA'}
            </span>
            <span className="inline-block border border-gray-500 px-1.5 py-0.5 text-gray-300">
              {active.isHd ? 'HD' : 'SD'}
            </span>
          </div>

          {/* Description */}
          <p className="hidden md:block max-w-[550px] text-gray-200 text-base leading-relaxed mb-6">
            {active.description}
          </p>

          {/* CTA Buttons */}
          <div className="flex items-center gap-5">
            <Link
              href={`/watch/${active.id}`}
              className="inline-flex items-center gap-2.5 bg-[#E50914] hover:bg-[#F6121D] text-white text-sm font-semibold uppercase tracking-wide px-7 py-3 transition-colors"
            >
              <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
                <path d="M8 5v14l11-7z" />
              </svg>
              Play
            </Link>
            <Link
              href={`/watch/${active.id}`}
              className="inline-flex items-center gap-2 bg-white/20 hover:bg-white/40 backdrop-blur-sm text-white text-sm font-semibold uppercase tracking-wide px-6 py-3 transition-colors"
              aria-label={`More information about ${active.title}`}
            >
              <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
                <path d="M12 2c5.515 0 10 4.485 10 10s-4.485 10-10 10S2 17.515 2 12 6.485 2 12 2zm0 7.5a1.75 1.75 0 100-3.5 1.75 1.75 0 000 3.5zm0 2c-.552 0-1 .448-1 1v5.75a1 1 0 002 0v-5.75c0-.552-.448-1-1-1z" />
              </svg>
              More Info
            </Link>
          </div>
        </div>
      </div>

      {/* Dot Indicators */}
      {items.length > 1 && (
        <div className="absolute bottom-4 right-8 z-10 flex items-center gap-2">
          {items.slice(0, 5).map((_, i) => (
            <button
              key={i}
              onClick={() => setActiveIndex(i)}
              className={`h-[2px] transition-all duration-300 ${
                i === activeIndex ? 'w-6 bg-white' : 'w-3 bg-white/40 hover:bg-white/70'
              }`}
              aria-label={`Go to slide ${i + 1}`}
            />
          ))}
        </div>
      )}
    </section>
  );
}