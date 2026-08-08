'use client';

import Link from 'next/link';
import { Content } from '@/types';

// Legacy wrapper - prefer MediaCard for new code
export function ContentCard({ content }: { content: Content }) {
  return (
    <Link href={`/watch/${content.id}`}>
      <div className="group cursor-pointer">
        <div className="relative h-64 w-full overflow-hidden bg-[#1f1f1f] mb-2">
          <div className="absolute inset-0 shimmer flex items-center justify-center">
            <span className="uppercase text-[10px] tracking-widest text-gray-700 font-semibold px-3">
              {content.type === 'movie' ? 'Film' : 'Series'}
            </span>
          </div>
          <div className="absolute inset-0 bg-black/60 opacity-0 group-hover:opacity-100 transition flex items-center justify-center">
            <button className="bg-white text-black px-8 py-3 font-semibold hover:bg-gray-300 transition">
              Play
            </button>
          </div>
        </div>
        <h3 className="text-white font-semibold truncate">{content.title}</h3>
        <p className="text-gray-400 text-sm">{content.releaseDate?.split('-')[0]}</p>
      </div>
    </Link>
  );
}