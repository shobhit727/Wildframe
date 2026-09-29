import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

import {
  clearTokens,
  getAccessToken,
  normalizeContent,
  normalizeSearchContentDocument,
  setTokens,
} from '@/api/client';
import type { BackendContent, BackendContentListItem } from '@/types';

describe('token helpers', () => {
  const access = 'access.token.value';
  const refresh = 'refresh.token.value';

  beforeEach(() => {
    localStorage.clear();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{}', { status: 200 })));
  });

  afterEach(() => {
    clearTokens();
    vi.unstubAllGlobals();
  });

  it('publishes access tokens after cookie persistence', async () => {
    await setTokens({ access_token: access, refresh_token: refresh });
    expect(getAccessToken()).toBe(access);
  });


  it('clears tokens', async () => {
    await setTokens({ access_token: access, refresh_token: refresh });
    clearTokens();
    expect(getAccessToken()).toBeNull();
  });

  it('keeps tokens out of localStorage (XSS hardening)', async () => {
    await setTokens({ access_token: access, refresh_token: refresh });
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


  it('normalizes the exact content-list response shape', () => {
    const listItem: BackendContentListItem = {
      id: 'list-1',
      title: 'Catalog Only',
      slug: 'catalog-only',
      description: 'Only fields returned by ContentListResponse.',
      content_type: 'movie',
      poster_url: null,
      imdb_rating: 8.1,
      audience_score: 81,
      is_premium: false,
      genres: [{ id: 'g1', name: 'Drama', slug: 'drama' }],
    };

    const normalized = normalizeContent(listItem);
    expect(normalized.title).toBe('Catalog Only');
    expect(normalized.rating).toBe(8.1);
    expect(normalized.matchPercentage).toBe(81);
    expect(normalized.duration).toBe(0);
  });

  it('normalizes search-service content documents', () => {
    const normalized = normalizeSearchContentDocument({
      id: 'search-1',
      title: 'Search Result',
      description: 'From the Elasticsearch document shape.',
      content_type: 'movie',
      genres: ['Drama'],
      actors: [],
      director: '',
      release_year: 2025,
      rating: 76,
      status: 'published',
    });

    expect(normalized.slug).toBe('search-result');
    expect(normalized.matchPercentage).toBe(76);
    // browse/page.tsx maps every payload through normalizeContent, so assert the
    // post-normalized shape the UI actually consumes (string[] genres), not the
    // pre-normalization BackendGenre[] the payload carries.
    expect(normalizeContent(normalized).genres).toEqual(['Drama']);
    expect(normalized.poster_url).toBeNull();
  });

  it('maps genre names arrays', () => {
    const c = normalizeContent(movie);
    expect(c.genre).toBe('sci-fi');
    expect(c.genres).toEqual(['sci-fi', 'action']);
  });

  it('keeps audience score scales separate for stars and Match', () => {
    const normalized = normalizeContent(movie);
    expect(normalized.rating).toBe(8.7);
    expect(normalized.matchPercentage).toBe(87);
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