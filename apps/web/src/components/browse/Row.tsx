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
    window.addEventListener('resize', checkScroll);
    return () => {
      el.removeEventListener('scroll', checkScroll);
      window.removeEventListener('resize', checkScroll);
    };
  }, [items]);

  const scroll = (direction: 'left' | 'right') => {
    const el = scrollRef.current;
    if (!el) return;
    const amount = el.clientWidth * 0.8;
    el.scrollBy({ left: direction === 'left' ? -amount : amount, behavior: 'smooth' });
  };

  if (items.length === 0) return null;

  return (
    <section className="relative nf-row">
      {/* Title */}
      <h2 className="text-base sm:text-lg font-semibold text-[#e5e5e5] mb-1 px-8">
        {title}
      </h2>

      {/* Scroll Container */}
      <div className="relative group">
        {/* Left Chevron */}
        {canScrollLeft && (
          <button
            onClick={() => scroll('left')}
            className="nf-chevron nf-chevron-left"
            aria-label={`Scroll ${title} left`}
          >
            <svg className="w-10 h-10" fill="currentColor" viewBox="0 0 24 24">
              <path d="M15.41 7.41L14 6l-6 6 6 6 1.41-1.41L10.83 12z" />
            </svg>
          </button>
        )}

        {/* Cards */}
        <div
          ref={scrollRef}
          className="flex gap-1.5 overflow-x-auto scrollbar-hide px-8 pb-2 pt-1 [-ms-overflow-style:none]"
        >
          {items.map((item) => (
            <div
              key={item.id}
              className={`flex-shrink-0 ${
                variant === 'poster' ? 'w-[138px] sm:w-[158px] lg:w-[188px]' : 'w-[240px] sm:w-[286px] lg:w-[330px]'
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

        {/* Right Chevron */}
        {canScrollRight && (
          <button
            onClick={() => scroll('right')}
            className="nf-chevron nf-chevron-right"
            aria-label={`Scroll ${title} right`}
          >
            <svg className="w-10 h-10" fill="currentColor" viewBox="0 0 24 24">
              <path d="M8.59 16.59L10 18l6-6-6-6-1.41 1.41L13.17 12z" />
            </svg>
          </button>
        )}
      </div>
    </section>
  );
}