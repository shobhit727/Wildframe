import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';

import WatchPage from '@/app/watch/[id]/page';
import { DEMO_HLS_URL } from '@/api/client';
import { useAuthStore } from '@/stores/auth';
import {
  renderWithQuery,
  makeBackendContent,
  makeBackendSeason,
  makePlaybackSession,
  makeUser,
} from '@/__tests__/app-helpers';

const { apiClient } = vi.hoisted(() => ({
  apiClient: {
    getContentById: vi.fn(),
    getSeasons: vi.fn(),
    getContentList: vi.fn(),
    startPlaybackSession: vi.fn(),
    getManifestForEpisode: vi.fn(),
  },
}));

vi.mock('@/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/client')>();
  return { ...actual, apiClient };
});

// The real player pulls in hls.js/dashjs and owns a <video> element; the page
// only needs to know it received a stream URL, so a stub keeps the test focused
// on the page's own behaviour.
vi.mock('@/components/player/VideoPlayer', () => ({
  VideoPlayer: ({ title, src }: { title?: string; src?: string }) => (
    <div data-testid="video-player" data-src={src ?? ''}>
      {title ?? 'untitled'}
    </div>
  ),
}));

const push = vi.fn();

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push, replace: vi.fn(), refresh: vi.fn() }),
  usePathname: () => '/watch/c-1',
  useParams: () => ({ id: 'c-1' }),
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

const MOVIE = makeBackendContent({ id: 'c-1', title: 'Neon Drift', content_type: 'movie' });
const SHOW = makeBackendContent({
  id: 'c-1',
  title: 'Orbital Drift',
  content_type: 'series',
  duration_minutes: 45,
  genres: [makeBackendContent().genres[0], { id: 'g-2', name: 'thriller', slug: 'thriller' }],
});
const SIMILAR = makeBackendContent({ id: 'c-9', title: 'Solar Winter' });

const LIST_KEY = 'wildframe_my_list:u-1';

/** Structural query: see the note in the browse page test. */
function similarHrefs(): string[] {
  return Array.from(document.querySelectorAll('a[href^="/watch/"]')).map((a) =>
    a.getAttribute('href') as string,
  );
}

/** Wait until the playback session has resolved and the detail view is up. */
async function waitForPlayer(): Promise<void> {
  await screen.findByTestId('video-player');
}

beforeEach(() => {
  push.mockReset();
  for (const fn of Object.values(apiClient)) fn.mockReset();
  localStorage.clear();

  apiClient.getContentById.mockResolvedValue(MOVIE);
  apiClient.getSeasons.mockResolvedValue([]);
  apiClient.getContentList.mockResolvedValue([SIMILAR]);
  apiClient.startPlaybackSession.mockResolvedValue(makePlaybackSession());
  apiClient.getManifestForEpisode.mockResolvedValue(null);

  useAuthStore.setState({
    user: makeUser(),
    token: 'tok',
    isAuthenticated: true,
    isLoading: false,
  });
});

describe('WatchPage (/watch/[id])', () => {
  describe('authentication', () => {
    it('redirects a signed-out visitor and starts no session', async () => {
      useAuthStore.setState({ user: null, token: null, isAuthenticated: false });

      renderWithQuery(<WatchPage />);

      await waitFor(() => expect(push).toHaveBeenCalledWith('/login'));
      // Opening a playback session without a user would create an orphaned
      // streaming-service row on every bot visit.
      expect(apiClient.startPlaybackSession).not.toHaveBeenCalled();
    });

    it('keeps the visitor on a loading state rather than a sign-in prompt', async () => {
      useAuthStore.setState({ user: null, token: null, isAuthenticated: false });

      renderWithQuery(<WatchPage />);

      // Reported as a bug: a signed-out visitor whose router push is a no-op
      // (or who is bounced straight back) sits on this spinner indefinitely —
      // there is no sign-in link or message anywhere in the view.
      expect(await screen.findByText('Starting playback...')).toBeInTheDocument();
      expect(screen.queryByText('Starting playback...')).toBeInTheDocument();
    });
  });

  describe('playback session', () => {
    it('shows a starting state until the session resolves', () => {
      apiClient.startPlaybackSession.mockReturnValue(new Promise(() => {}));

      renderWithQuery(<WatchPage />);

      expect(screen.getByText('Starting playback...')).toBeInTheDocument();
      expect(screen.queryByTestId('video-player')).toBeNull();
    });

    it('opens a session for the current user, content and web player', async () => {
      renderWithQuery(<WatchPage />);
      await waitForPlayer();

      expect(apiClient.startPlaybackSession).toHaveBeenCalledWith({
        user_id: 'u-1',
        content_id: 'c-1',
        episode_id: undefined,
        device_id: 'web-player',
      });
    });

    it('mounts the player once the session resolves', async () => {
      renderWithQuery(<WatchPage />);

      expect(await screen.findByTestId('video-player')).toBeInTheDocument();
      expect(screen.queryByText('Starting playback...')).toBeNull();
    });

    it('points the player at the demo stream for a movie', async () => {
      renderWithQuery(<WatchPage />);
      await waitForPlayer();

      expect(screen.getByTestId('video-player')).toHaveAttribute('data-src', DEMO_HLS_URL);
    });

    it('hands the movie the demo preview stream when nothing is packaged', async () => {
      renderWithQuery(<WatchPage />);
      await waitForPlayer();

      // media-pipeline emits stub manifests, so the demo stream is the expected
      // path and the page must disclose it rather than pretending it is real.
      expect(
        await screen.findByText('Preview stream — no packaged media for this title yet.'),
      ).toBeInTheDocument();
    });

    it('bails out to browse and reports the failure when the session cannot start', async () => {
      apiClient.startPlaybackSession.mockRejectedValue(new Error('streaming 503'));

      renderWithQuery(<WatchPage />);

      // A silent stall here would leave the viewer on a spinner forever with no
      // indication that playback is broken.
      await waitFor(() => expect(push).toHaveBeenCalledWith('/browse'));
      expect(screen.getByText('Starting playback...')).toBeInTheDocument();
    });
  });

  describe('movie detail', () => {
    it('shows the title, description and genres', async () => {
      renderWithQuery(<WatchPage />);
      await waitForPlayer();

      expect(
        await screen.findByRole('heading', { level: 1, name: 'Neon Drift' }),
      ).toBeInTheDocument();
      expect(
        screen.getByText('A courier outruns a corporate drone across a flooded megacity.'),
      ).toBeInTheDocument();
      expect(screen.getByText('sci-fi')).toBeInTheDocument();
    });

    it('shows the year, runtime and maturity badges', async () => {
      renderWithQuery(<WatchPage />);
      await waitForPlayer();

      await screen.findByRole('heading', { level: 1, name: 'Neon Drift' });
      expect(screen.getByText('2024')).toBeInTheDocument();
      expect(screen.getByText('1h 58m')).toBeInTheDocument();
      expect(screen.getByText('PG-13')).toBeInTheDocument();
      expect(screen.getByText('HD')).toBeInTheDocument();
    });

    it('pins the match badge at 99% for a 0-100 audience score', async () => {
      // BUG (reported): normalizeContent() copies `audience_score` (0-100) into
      // `rating`, but this badge computes `rating * 10` as if it were a 0-10
      // scale. Any audience score of 10 or more saturates the clamp at 99, so
      // the entire catalogue advertises a 99% match.
      expect(MOVIE.audience_score).toBe(87);
      renderWithQuery(<WatchPage />);
      await waitForPlayer();

      // The detail badge lives in the row that also holds the title.
      const heading = await screen.findByRole('heading', { level: 1, name: 'Neon Drift' });
      const detailRow = heading.parentElement?.parentElement as HTMLElement;
      expect(within(detailRow).getByText('99% Match')).toBeInTheDocument();

      // The same arithmetic in MediaCard.tsx saturates the suggestion cards too.
      await screen.findByRole('heading', { level: 2, name: 'More Like This' });
      expect(screen.getAllByText('99% Match')).toHaveLength(2);
    });

    it('never reports a match below 75% even for a poorly rated title', async () => {
      // Same bug, other end of the clamp: the `Math.max(75, ...)` floor means a
      // 1/10 title still claims 75% confidence.
      apiClient.getContentById.mockResolvedValue(
        makeBackendContent({ id: 'c-1', title: 'Neon Drift', audience_score: 1 }),
      );

      renderWithQuery(<WatchPage />);
      await waitForPlayer();

      expect(await screen.findByText('75% Match')).toBeInTheDocument();
    });

    it('omits the episode list for a movie', async () => {
      renderWithQuery(<WatchPage />);
      await waitForPlayer();
      await screen.findByRole('heading', { level: 1, name: 'Neon Drift' });

      expect(screen.queryByText(/^Episodes$/)).toBeNull();
      expect(apiClient.getSeasons).not.toHaveBeenCalled();
    });

    it('passes the resolved title to the player', async () => {
      renderWithQuery(<WatchPage />);
      await waitForPlayer();

      await waitFor(() => expect(screen.getByTestId('video-player')).toHaveTextContent('Neon Drift'));
    });
  });

  describe('series detail', () => {
    beforeEach(() => {
      apiClient.getContentById.mockResolvedValue(SHOW);
      apiClient.getSeasons.mockResolvedValue([makeBackendSeason()]);
    });

    it('lists the active season with its episode count', async () => {
      renderWithQuery(<WatchPage />);
      await waitForPlayer();

      expect(
        await screen.findByRole('heading', { level: 2, name: 'Season 1 · 2 episodes' }),
      ).toBeInTheDocument();
    });

    it('starts on the demo stream before an episode is chosen', async () => {
      renderWithQuery(<WatchPage />);
      await waitForPlayer();

      expect(apiClient.getManifestForEpisode).not.toHaveBeenCalled();
      expect(
        await screen.findByText('Preview stream — no packaged media for this title yet.'),
      ).toBeInTheDocument();
    });

    it('switches to a new session and names the episode in the heading', async () => {
      renderWithQuery(<WatchPage />);
      await waitForPlayer();
      await screen.findByRole('heading', { level: 2, name: 'Season 1 · 2 episodes' });

      fireEvent.click(screen.getByRole('button', { name: /Episode 2: Dead Reckoning/ }));

      expect(
        await screen.findByRole('heading', { level: 1, name: /Orbital Drift · Ep 2/ }),
      ).toBeInTheDocument();
      await waitFor(() =>
        expect(apiClient.startPlaybackSession).toHaveBeenCalledWith(
          expect.objectContaining({ episode_id: 'ep-2' }),
        ),
      );
    });

    it('shows the starting state again while the new session is created', async () => {
      renderWithQuery(<WatchPage />);
      await waitForPlayer();
      await screen.findByRole('heading', { level: 2, name: 'Season 1 · 2 episodes' });

      let release: (() => void) | undefined;
      apiClient.startPlaybackSession.mockImplementationOnce(
        () => new Promise((resolve) => {
          release = () => resolve(makePlaybackSession({ id: 'sess-2' }));
        })
      );

      fireEvent.click(screen.getByRole('button', { name: /Episode 1: Cold Start/ }));

      // Without the re-entry guard the old video keeps playing the previous
      // episode while the new one is being negotiated.
      expect(await screen.findByText('Starting playback...')).toBeInTheDocument();
      release?.();
      await waitForPlayer();
    });

    it('prefers the episode manifest over the demo stream', async () => {
      apiClient.getManifestForEpisode.mockResolvedValue({
        id: 'm-1',
        episode_id: 'ep-1',
        content_id: 'c-1',
        protocol: 'hls',
        manifest_url: 'https://cdn.test/ep-1/master.m3u8',
        variants: [],
        available_bitrates: [],
      });

      renderWithQuery(<WatchPage />);
      await waitForPlayer();
      await screen.findByRole('heading', { level: 2, name: 'Season 1 · 2 episodes' });

      fireEvent.click(screen.getByRole('button', { name: /Episode 1: Cold Start/ }));

      await waitFor(() => expect(apiClient.getManifestForEpisode).toHaveBeenCalledWith('ep-1'));
      // A real manifest must replace the demo fallback outright, including the
      // "preview stream" disclosure.
      expect(screen.getByTestId('video-player')).toHaveAttribute(
        'data-src',
        'https://cdn.test/ep-1/master.m3u8',
      );
      expect(
        screen.queryByText('Preview stream — no packaged media for this title yet.'),
      ).toBeNull();
    });

    it('falls back to the demo stream when the manifest lookup fails', async () => {
      apiClient.getManifestForEpisode.mockResolvedValue(null);

      renderWithQuery(<WatchPage />);
      await waitForPlayer();
      await screen.findByRole('heading', { level: 2, name: 'Season 1 · 2 episodes' });

      fireEvent.click(screen.getByRole('button', { name: /Episode 1: Cold Start/ }));

      // getManifestForEpisode swallows its own errors and returns null, so the
      // page must still produce a playable stream.
      expect(
        await screen.findByText('Preview stream — no packaged media for this title yet.'),
      ).toBeInTheDocument();
    });

    it('joins every genre for the series', async () => {
      renderWithQuery(<WatchPage />);
      await waitForPlayer();

      expect(await screen.findByText('sci-fi, thriller')).toBeInTheDocument();
    });
  });

  describe('more like this', () => {
    it('suggests other titles', async () => {
      renderWithQuery(<WatchPage />);
      await waitForPlayer();

      expect(await screen.findByRole('heading', { level: 2, name: 'More Like This' })).toBeInTheDocument();
      expect(similarHrefs()).toEqual(['/watch/c-9']);
    });

    it('never suggests the title already being watched', async () => {
      apiClient.getContentList.mockResolvedValue([SIMILAR, MOVIE]);

      renderWithQuery(<WatchPage />);
      await waitForPlayer();

      await screen.findByRole('heading', { level: 2, name: 'More Like This' });
      // Linking the current title into its own "More Like This" rail is a
      // dead end for the viewer.
      expect(similarHrefs()).toEqual(['/watch/c-9']);
    });

    it('omits the rail entirely when nothing else matches', async () => {
      apiClient.getContentList.mockResolvedValue([MOVIE]);

      renderWithQuery(<WatchPage />);
      await waitForPlayer();

      expect(screen.queryByRole('heading', { level: 2, name: 'More Like This' })).toBeNull();
    });
  });

  describe('my list', () => {
    it('adds the title to the local watchlist', async () => {
      renderWithQuery(<WatchPage />);
      await waitForPlayer();

      fireEvent.click(screen.getByRole('button', { name: 'Toggle my list' }));

      await waitFor(() =>
        expect(JSON.parse(localStorage.getItem(LIST_KEY) ?? '[]')).toEqual(['c-1']),
      );
    });

    it('removes a title that is already saved', async () => {
      localStorage.setItem(LIST_KEY, JSON.stringify(['c-1']));

      renderWithQuery(<WatchPage />);
      await waitForPlayer();

      fireEvent.click(screen.getByRole('button', { name: 'Toggle my list' }));

      await waitFor(() => expect(JSON.parse(localStorage.getItem(LIST_KEY) ?? '[]')).toEqual([]));
    });

    it('appends without dropping existing saves', async () => {
      localStorage.setItem(LIST_KEY, JSON.stringify(['c-9']));

      renderWithQuery(<WatchPage />);
      await waitForPlayer();

      fireEvent.click(screen.getByRole('button', { name: 'Toggle my list' }));

      await waitFor(() =>
        expect(JSON.parse(localStorage.getItem(LIST_KEY) ?? '[]')).toEqual(['c-9', 'c-1']),
      );
    });

    it('does nothing for a signed-out viewer', async () => {
      useAuthStore.setState({ user: null, token: null, isAuthenticated: false });

      renderWithQuery(<WatchPage />);
      await waitFor(() => expect(push).toHaveBeenCalledWith('/login'));

      // The page never renders its detail view without a session, so there is
      // no toggle to press; assert the store was left alone regardless.
      expect(localStorage.getItem(LIST_KEY)).toBeNull();
    });
  });

  describe('navigation', () => {
    it('offers a way back to browse from both ends of the page', async () => {
      renderWithQuery(<WatchPage />);
      await waitForPlayer();

      const backLinks = screen.getAllByRole('link', { name: /Back to Browse/ });
      expect(backLinks).toHaveLength(2);
      for (const link of backLinks) {
        expect(link).toHaveAttribute('href', '/browse');
      }
    });
  });

  describe('failure paths', () => {
    it('still plays the stream when the content lookup fails', async () => {
      apiClient.getContentById.mockRejectedValue(new Error('content 500'));

      renderWithQuery(<WatchPage />);

      // A failed detail lookup must not block playback — the viewer gets the
      // stream, just without metadata.
      await waitForPlayer();
      expect(screen.queryByRole('heading', { level: 1 })).toBeNull();
      expect(
        await screen.findByText('Preview stream — no packaged media for this title yet.'),
      ).toBeInTheDocument();
    });

    it('does not recover the metadata on its own after a failed lookup', async () => {
      apiClient.getContentById.mockRejectedValue(new Error('content 500'));

      renderWithQuery(<WatchPage />);
      await waitForPlayer();

      // Reported as a gap: the content query has no retry and nothing on the
      // page re-triggers it, so one transient content-service blip leaves the
      // viewer watching a titleless page until they navigate away and back.
      expect(screen.queryByRole('heading', { level: 1 })).toBeNull();
      expect(apiClient.getContentById).toHaveBeenCalledTimes(1);
    });
  });
});
