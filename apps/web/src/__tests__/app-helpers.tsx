/**
 * Shared helpers for the App Router page tests.
 *
 * This file is NOT a test file (it has no ".test." / ".spec." name) so Vitest
 * will not collect it — it exists purely to avoid re-declaring the same
 * QueryClient wrapper and fixture factories in every page test.
 *
 * Note on the QueryClient: the app singleton in src/utils/queryClient.ts uses
 * staleTime 5min and retry 2, which makes a rejected query retry for ~10s and
 * caches data across tests. Every client built here uses retry:false and zero
 * timeouts so a failing query settles immediately and a passing one never leaks
 * state into the next test.
 */
import type { ReactNode } from 'react';
import { render } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { BackendContent, BackendEpisode, BackendSeason, User } from '@/types';

/** Build a QueryClient tuned for tests: no retries, no cross-test caching. */
export function createTestQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: 0, gcTime: 0, refetchOnWindowFocus: false },
      mutations: { retry: false },
    },
  });
}

/** Render `ui` inside a fresh QueryClientProvider. Returns the client for assertions. */
export function renderWithQuery(ui: ReactNode) {
  const client = createTestQueryClient();
  const result = render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
  return { ...result, queryClient: client };
}

/** Authenticated user fixture (plain object — no need to run it through normalizeUser). */
export function makeUser(overrides: Partial<User> = {}): User {
  return {
    id: 'u-1',
    email: 'ada@wildframe.test',
    firstName: 'Ada',
    lastName: 'Lovelace',
    emailVerified: true,
    role: 'user',
    ...overrides,
  };
}

export function makeAdminUser(overrides: Partial<User> = {}): User {
  return makeUser({ id: 'admin-1', firstName: 'Grace', lastName: 'Hopper', role: 'admin', ...overrides });
}

/** Backend content DTO fixture, shaped like a content-service response. */
export function makeBackendContent(overrides: Partial<BackendContent> = {}): BackendContent {
  return {
    id: 'c-1',
    title: 'Neon Drift',
    slug: 'neon-drift',
    description: 'A courier outruns a corporate drone across a flooded megacity.',
    content_type: 'movie',
    status: 'published',
    release_date: '2024-05-01',
    duration_minutes: 118,
    poster_url: 'https://cdn.test/neon-drift.jpg',
    backdrop_url: 'https://cdn.test/neon-drift-backdrop.jpg',
    audience_score: 87,
    content_rating: 'PG-13',
    is_hd: true,
    // `ContentResponse.is_premium` is a required bool on the backend, so a real
    // response always carries it.
    is_premium: false,
    genres: [{ id: 'g-1', name: 'sci-fi', slug: 'sci-fi' }],
    ...overrides,
  };
}

export function makeBackendEpisode(overrides: Partial<BackendEpisode> = {}): BackendEpisode {
  return {
    id: 'ep-1',
    episode_number: 1,
    title: 'Cold Start',
    duration_minutes: 42,
    release_date: '2024-05-02',
    ...overrides,
  };
}

export function makeBackendSeason(overrides: Partial<BackendSeason> = {}): BackendSeason {
  return {
    id: 's-1',
    season_number: 1,
    episode_count: 2,
    episodes: [
      makeBackendEpisode(),
      makeBackendEpisode({ id: 'ep-2', episode_number: 2, title: 'Dead Reckoning' }),
    ],
    ...overrides,
  };
}

/** Playback session fixture for the watch page / my-list history. */
export function makePlaybackSession(overrides: Record<string, unknown> = {}) {
  return {
    id: 'sess-1',
    user_id: 'u-1',
    content_id: 'c-1',
    episode_id: null,
    device_id: 'web-player',
    status: 'active',
    current_position_seconds: 0,
    total_duration_seconds: 0,
    protocol: 'hls',
    resolution: '1080p',
    bitrate_kbps: 5000,
    started_at: '2024-05-01T00:00:00Z',
    last_activity_at: '2024-05-01T00:10:00Z',
    ended_at: null,
    ...overrides,
  };
}
