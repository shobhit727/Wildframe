'use client';

import { useRef, useState, useEffect } from 'react';
import { Content } from '@/types';
import { MediaCard } from './MediaCard';

interface RowProps {
  title: string;
  items: Content[];
  variant?: 'poster' | 'backdrop';
  showProgress?: Record<string, number>; // contentId -> progress percentage
}

export function Row({ title, items, variant = 'poster', showProgress }: RowProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [canScrollLeft, setCanScrollLeft] = useState(false);
  const [canScrollRight, setCanScrollRight] = useState(false);

  const checkScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    setCanScrollLeft(el.scrollLeft > 10);
    setCanScrollRight(el.scrollLeft < el.scrollWidth - el.clientWidth - 10);
  };

  useEffect(() => {
    checkScroll();
    const el = scrollRef.current;
    if (!el) return;
    el.addEventListener('scroll', checkScroll, { passive: true });
    return () => el.removeEventListener('scroll', checkScroll);
  }, [items]);

  const scroll = (direction: 'left' | 'right') => {
    const el = scrollRef.current;
    if (!el) return;
    const amount = el.clientWidth * 0.8;
    el.scrollBy({
      left: direction === 'left' ? -amount : amount,
      behavior: 'smooth',
    });
  };

  if (items.length === 0) return null;

  return (
    <section className="relative group/row">
      {/* Title */}
      <h2 className="text-lg sm:text-xl font-bold text-white mb-3 px-4 sm:px-6 lg:px-8">
        {title}
      </h2>

      {/* Scroll Container */}
      <div className="relative">
        {/* Left Arrow */}
        {canScrollLeft && (
          <button
            onClick={() => scroll('left')}
            className="absolute left-0 top-0 bottom-0 z-10 w-12 bg-gradient-to-r from-dark-950 to-transparent flex items-center justify-start pl-1 opacity-0 group-hover/row:opacity-100 transition-opacity"
            aria-label="Scroll left"
          >
            <svg className="w-8 h-8 text-white drop-shadow-lg" fill="currentColor" viewBox="0 0 24 24">
              <path d="M15.41 7.41L14 6l-6 6 6 6 1.41-1.41L10.83 12z" />
            </svg>
          </button>
        )}

        {/* Cards */}
        <div
          ref={scrollRef}
          className="flex gap-3 overflow-x-auto scrollbar-hide px-4 sm:px-6 lg:px-8 pb-2"
        >
          {items.map((item) => (
            <div
              key={item.id}
              className={`flex-shrink-0 ${
                variant === 'poster' ? 'w-[140px] sm:w-[160px] lg:w-[180px]' : 'w-[260px] sm:w-[300px] lg:w-[340px]'
              }`}
            >
              <MediaCard
                content={item}
                variant={variant}
                showProgress={showProgress?.[item.id]}
              />
            </div>
          ))}
        </div>

        {/* Right Arrow */}
        {canScrollRight && (
          <button
            onClick={() => scroll('right')}
            className="absolute right-0 top-0 bottom-0 z-10 w-12 bg-gradient-to-l from-dark-950 to-transparent flex items-center justify-end pr-1 opacity-0 group-hover/row:opacity-100 transition-opacity"
            aria-label="Scroll right"
          >
            <svg className="w-8 h-8 text-white drop-shadow-lg" fill="currentColor" viewBox="0 0 24 24">
              <path d="M8.59 16.59L10 18l6-6-6-6-1.41 1.41L13.17 12z" />
            </svg>
          </button>
        )}
      </div>
    </section>
  );
}
