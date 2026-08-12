import { describe, it, expect, beforeEach, afterEach } from 'vitest';

import {
  clearTokens,
  getAccessToken,
  normalizeContent,
  setTokens,
} from '@/api/client';
import type { BackendContent } from '@/types';

describe('token helpers', () => {
  const access = 'access.token.value';
  const refresh = 'refresh.token.value';

  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    clearTokens();
  });

  it('stores access and refresh tokens', () => {
    setTokens({ access_token: access, refresh_token: refresh });
    expect(getAccessToken()).toBe(access);
  });

  it('returns null when no access token is stored', () => {
    expect(getAccessToken()).toBeNull();
  });

  it('clears tokens', () => {
    setTokens({ access_token: access, refresh_token: refresh });
    clearTokens();
    expect(getAccessToken()).toBeNull();
  });

  it('keeps tokens out of localStorage (XSS hardening)', () => {
    setTokens({ access_token: access, refresh_token: refresh });
    expect(localStorage.getItem('accessToken')).toBeNull();
    expect(localStorage.getItem('refreshToken')).toBeNull();
    expect(localStorage.getItem('user')).toBeNull();
  });
});

describe('normalizeContent', () => {
  const movie: BackendContent = {
    id: 'c1',
    title: 'Neon Drift',
    slug: 'neon-drift',
    description: 'A cyberpunk racing drama.',
    content_type: 'movie',
    status: 'published',
    genres: [{ id: 'g1', name: 'sci-fi', slug: 'sci-fi' }, { id: 'g2', name: 'action', slug: 'action' }],
    poster_url: 'https://example.com/poster.jpg',
    backdrop_url: null,
    duration_minutes: 118,
    release_date: '2024-05-01',
    audience_score: 87,
  };

  it('maps genre names arrays', () => {
    const c = normalizeContent(movie);
    expect(c.genre).toBe('sci-fi');
    expect(c.genres).toEqual(['sci-fi', 'action']);
  });

  it('maps series content_type to show type', () => {
    const show = normalizeContent({ ...movie, content_type: 'show' });
    expect(show.type).toBe('show');
  });

  it('falls back to Other when no genres', () => {
    const c = normalizeContent({ ...movie, genres: [] });
    expect(c.genre).toBe('Other');
  });

  it('falls back poster for backdrop', () => {
    const c = normalizeContent({ ...movie, backdrop_url: undefined });
    expect(c.backdrop).toBe(movie.poster_url);
    expect(c.poster).toBe(movie.poster_url);
  });
});