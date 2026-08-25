'use client';

import { CSSProperties, useMemo } from 'react';

/**
 * Deterministic, generated artwork for titles.
 *
 * Every title gets a unique cinematic composition derived from a hash of its
 * id + title: palette selection, gradient geometry, light position, and
 * typographic treatment are all seeded. No remote assets required.
 */

interface PosterArtProps {
  seed: string;
  title: string;
  variant?: 'poster' | 'backdrop';
  className?: string;
}

// Curated cinematic palettes: [base dark, primary glow, secondary accent].
const PALETTES: ReadonlyArray<readonly [string, string, string]> = [
  ['#0d1b2a', '#1b6ca8', '#f77f00'], // deep sea / signal orange
  ['#1a0b2e', '#7b2cbf', '#ff5d8f'], // violet dusk / magenta
  ['#0b2b26', '#2a9d8f', '#e9c46a'], // forest / gold
  ['#2b0b0e', '#e50914', '#ffa36c'], // ember noir
  ['#101418', '#4cc9f0', '#b5179e'], // arctic / neon rose
  ['#191308', '#e9a03b', '#7fb069'], // amber field
  ['#12060e', '#c9184a', '#5a189a'], // crimson haze
  ['#04151f', '#00a896', '#f4d35e'], // lagoon
];

function hashSeed(seed: string): number {
  let h = 2166136261;
  for (let i = 0; i < seed.length; i++) {
    h ^= seed.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

function mulberry32(a: number): () => number {
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function PosterArt({ seed, title, variant = 'poster', className = '' }: PosterArtProps) {
  const art = useMemo(() => {
    const rand = mulberry32(hashSeed(seed || title || 'wildframe'));
    const [dark, primary, accent] = PALETTES[Math.floor(rand() * PALETTES.length)];

    const gx1 = Math.round(20 + rand() * 60); // glow x %
    const gy1 = Math.round(10 + rand() * 45); // glow y %
    const angle = Math.round(rand() * 360);
    const ringSize = Math.round(38 + rand() * 34); // % of width

    return { dark, primary, accent, gx1, gy1, angle, ringSize };
  }, [seed, title]);

  const isPoster = variant === 'poster';

  const style: CSSProperties = {
    backgroundImage: [
      `radial-gradient(circle at ${art.gx1}% ${art.gy1}%, ${art.primary}55 0%, transparent ${art.ringSize}%)`,
      `radial-gradient(circle at ${100 - art.gx1}% ${Math.min(95, art.gy1 + 35)}%, ${art.accent}33 0%, transparent 46%)`,
      `linear-gradient(${art.angle}deg, ${art.dark} 0%, #0b0b0b 130%)`,
    ].join(', '),
  };

  const initials = title
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase() ?? '')
    .join('');

  return (
    <div className={`absolute inset-0 overflow-hidden ${className}`} style={style} aria-hidden="true">
      {/* Light sweep */}
      <div
        className="absolute inset-0"
        style={{
          background: `linear-gradient(115deg, transparent 30%, ${art.primary}22 46%, transparent 60%)`,
        }}
      />

      {/* Brand frame motif */}
      <div
        className={`absolute border border-white/12 ${
          isPoster ? 'inset-[7%] rounded-sm' : 'inset-[3%_5%] rounded-sm'
        }`}
      />
      <div
        className={`absolute bg-gradient-to-b from-transparent to-black/70 ${
          isPoster ? 'inset-x-0 bottom-0 h-1/2' : 'inset-x-0 bottom-0 h-2/3'
        }`}
      />

      {/* Typographic treatment */}
      <div
        className={`absolute inset-0 flex flex-col items-center justify-center gap-2 px-4 text-center ${
          isPoster ? 'pt-[42%]' : 'pb-[16%] pt-[8%]'
        }`}
      >
        <span
          className={`font-black leading-none tracking-tight text-white/85 drop-shadow-[0_2px_14px_rgba(0,0,0,0.65)] ${
            isPoster ? 'text-5xl sm:text-6xl' : 'text-6xl sm:text-7xl lg:text-8xl'
          }`}
          style={{ letterSpacing: '-0.02em' }}
        >
          {initials}
        </span>
        {isPoster && (
          <span className="max-w-full truncate px-2 text-[10px] font-semibold uppercase tracking-[0.22em] text-white/55">
            {title}
          </span>
        )}
      </div>

      {/* Film grain */}
      <svg className="absolute inset-0 h-full w-full opacity-[0.16] mix-blend-overlay">
        <filter id={`grain-${hashSeed(seed)}`}>
          <feTurbulence type="fractalNoise" baseFrequency="0.82" numOctaves="2" stitchTiles="stitch" />
          <feColorMatrix type="saturate" values="0" />
        </filter>
        <rect width="100%" height="100%" filter={`url(#grain-${hashSeed(seed)})`} />
      </svg>

      {/* Vignette */}
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_center,transparent_52%,rgba(0,0,0,0.5)_100%)]" />
    </div>
  );
}
