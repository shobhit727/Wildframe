import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';

import MyListPage from '@/app/my-list/page';
import { useAuthStore } from '@/stores/auth';
import {
  renderWithQuery,
  makeBackendContent,
  makePlaybackSession,
  makeUser,
} from '@/__tests__/app-helpers';

const { apiClient } = vi.hoisted(() => ({
  apiClient: {
    getWatchHistory: vi.fn(),
    getContentById: vi.fn(),
  },
}));

vi.mock('@/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/client')>();
  return { ...actual, apiClient };
});

const push = vi.fn();

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push, replace: vi.fn(), refresh: vi.fn() }),
  usePathname: () => '/my-list',
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

const LIST_KEY = 'wildframe_my_list:u-1';

const DRIFT = makeBackendContent({ id: 'm-1', title: 'Neon Drift' });
const BLOOM = makeBackendContent({ id: 'm-2', title: 'Static Bloom' });

function historyItem(current: number, total: number, content = DRIFT) {
  return {
    session: makePlaybackSession({
      current_position_seconds: current,
      total_duration_seconds: total,
      content_id: content.id,
    }),
    content,
  };
}

/** Remove control for a saved title, located by its aria-label. */
function removeButton(title: string): HTMLButtonElement {
  const el = document.querySelector<HTMLButtonElement>(
    `button[aria-label="Remove ${title} from My List"]`,
  );
  if (!el) throw new Error(`remove button for "${title}" not found`);
  return el;
}

/** Structural query: see the note in the browse page test. */
function watchHrefs(): string[] {
  return Array.from(document.querySelectorAll('a[href^="/watch/"]')).map((a) =>
    a.getAttribute('href') as string,
  );
}

function openWatchlistTab() {
  fireEvent.click(screen.getByRole('button', { name: /My List/ }));
}

beforeEach(() => {
  push.mockReset();
  for (const fn of Object.values(apiClient)) fn.mockReset();
  localStorage.clear();

  apiClient.getWatchHistory.mockResolvedValue([]);
  apiClient.getContentById.mockImplementation(async (id: string) => {
    if (id === 'm-1') return DRIFT;
    if (id === 'm-2') return BLOOM;
    throw new Error(`unknown content ${id}`);
  });

  useAuthStore.setState({
    user: makeUser(),
    token: 'tok',
    isAuthenticated: true,
    isLoading: false,
  });
});

describe('MyListPage (/my-list)', () => {
  describe('authentication gate', () => {
    it('redirects a signed-out visitor without reading the list', async () => {
      useAuthStore.setState({ user: null, token: null, isAuthenticated: false });

      renderWithQuery(<MyListPage />);

      expect(await screen.findByText('Loading...')).toBeInTheDocument();
      expect(push).toHaveBeenCalledWith('/login');
      expect(apiClient.getWatchHistory).not.toHaveBeenCalled();
    });
  });

  describe('continue watching tab', () => {
    it('shows an empty state when there are no active sessions', async () => {
      renderWithQuery(<MyListPage />);

      expect(await screen.findByText('Nothing in progress')).toBeInTheDocument();
      expect(
        screen.getByText('Start watching a title and it will show up here.'),
      ).toBeInTheDocument();
    });

    it('links each in-progress title to its watch page', async () => {
      apiClient.getWatchHistory.mockResolvedValue([
        historyItem(300, 1200),
        historyItem(60, 1200, BLOOM),
      ]);

      renderWithQuery(<MyListPage />);

      await waitFor(() => expect(watchHrefs()).toEqual(['/watch/m-1', '/watch/m-2']));
      // The card must surface the title as text, not just as a link target.
      const card = document.querySelector('a[href="/watch/m-1"]');
      expect(card?.textContent).toContain('Neon Drift');
    });

    it('hides orphaned sessions whose content no longer resolves', async () => {
      // streaming-service can outlive a deleted content row; showing a blank
      // card would send the viewer to a 404.
      apiClient.getWatchHistory.mockResolvedValue([
        { session: makePlaybackSession({ content_id: 'gone' }), content: null },
      ]);

      renderWithQuery(<MyListPage />);

      expect(await screen.findByText('Nothing in progress')).toBeInTheDocument();
      expect(watchHrefs()).toEqual([]);
    });

    it('caps the progress bar so a nearly finished title never reads as complete', async () => {
      // 98% raw progress would render a bar indistinguishable from "finished";
      // the page clamps to 95% so the viewer can still see there is more.
      apiClient.getWatchHistory.mockResolvedValue([historyItem(980, 1000)]);

      const { container } = renderWithQuery(<MyListPage />);

      await waitFor(() =>
        expect(container.querySelector('div[style*="width: 95%"]')).not.toBeNull(),
      );
      expect(container.querySelector('div[style*="width: 98%"]')).toBeNull();
    });

    it('renders no progress bar when nothing has been watched yet', async () => {
      apiClient.getWatchHistory.mockResolvedValue([historyItem(0, 1200)]);

      const { container } = renderWithQuery(<MyListPage />);

      await waitFor(() => expect(watchHrefs()).toEqual(['/watch/m-1']));
      expect(container.querySelector('div[style*="width:"]')).toBeNull();
    });

    it('shows a placeholder progress bar when the duration is unknown', async () => {
      // total_duration_seconds of 0 (live/unknown length) used to divide by
      // zero; the page falls back to a small 10% bar instead.
      apiClient.getWatchHistory.mockResolvedValue([historyItem(45, 0)]);

      const { container } = renderWithQuery(<MyListPage />);

      await waitFor(() =>
        expect(container.querySelector('div[style*="width: 10%"]')).not.toBeNull(),
      );
    });

    it('surfaces the empty state when the history request fails', async () => {
      apiClient.getWatchHistory.mockRejectedValue(new Error('streaming 503'));

      renderWithQuery(<MyListPage />);

      expect(await screen.findByText('Nothing in progress')).toBeInTheDocument();
    });
  });

  describe('my list tab', () => {
    it('shows an empty state when nothing is saved', async () => {
      renderWithQuery(<MyListPage />);
      await screen.findByText('Nothing in progress');

      openWatchlistTab();

      expect(screen.getByText('Your list is empty')).toBeInTheDocument();
      expect(
        screen.getByText('Tap the My List button on any title to save it here.'),
      ).toBeInTheDocument();
      // Nothing is saved, so no per-id lookups should be issued.
      expect(apiClient.getContentById).not.toHaveBeenCalled();
    });

    it('resolves every saved id from the local watchlist', async () => {
      localStorage.setItem(LIST_KEY, JSON.stringify(['m-1', 'm-2']));

      renderWithQuery(<MyListPage />);
      await screen.findByText('Nothing in progress');

      openWatchlistTab();

      await waitFor(() => expect(watchHrefs()).toEqual(['/watch/m-1', '/watch/m-2']));
      expect(apiClient.getContentById).toHaveBeenCalledWith('m-1');
      expect(apiClient.getContentById).toHaveBeenCalledWith('m-2');
    });

    it('ignores non-string entries written by older clients', async () => {
      localStorage.setItem(LIST_KEY, JSON.stringify(['m-1', 42, null, { id: 'x' }]));

      renderWithQuery(<MyListPage />);
      await screen.findByText('Nothing in progress');
      openWatchlistTab();

      await waitFor(() => expect(watchHrefs()).toEqual(['/watch/m-1']));
      expect(apiClient.getContentById).toHaveBeenCalledTimes(1);
    });

    it('survives a corrupt localStorage payload', async () => {
      localStorage.setItem(LIST_KEY, '{not json');

      renderWithQuery(<MyListPage />);
      await screen.findByText('Nothing in progress');
      openWatchlistTab();

      // A malformed payload must not blank the whole page; it reads as empty.
      expect(screen.getByText('Your list is empty')).toBeInTheDocument();
    });

    it('skips a saved title the content service can no longer resolve', async () => {
      localStorage.setItem(LIST_KEY, JSON.stringify(['m-1', 'm-deleted']));

      renderWithQuery(<MyListPage />);
      await screen.findByText('Nothing in progress');
      openWatchlistTab();

      await waitFor(() => expect(watchHrefs()).toEqual(['/watch/m-1']));
    });

    it('removes a title and persists the shorter list', async () => {
      localStorage.setItem(LIST_KEY, JSON.stringify(['m-1', 'm-2']));

      renderWithQuery(<MyListPage />);
      await screen.findByText('Nothing in progress');
      openWatchlistTab();
      await waitFor(() => expect(watchHrefs()).toEqual(['/watch/m-1', '/watch/m-2']));

      fireEvent.click(removeButton('Neon Drift'));

      await waitFor(() => expect(watchHrefs()).toEqual(['/watch/m-2']));
      expect(JSON.parse(localStorage.getItem(LIST_KEY) ?? '[]')).toEqual(['m-2']);
    });

    it('empties the tab when the last title is removed', async () => {
      localStorage.setItem(LIST_KEY, JSON.stringify(['m-1']));

      renderWithQuery(<MyListPage />);
      await screen.findByText('Nothing in progress');
      openWatchlistTab();
      await waitFor(() => expect(watchHrefs()).toEqual(['/watch/m-1']));

      fireEvent.click(removeButton('Neon Drift'));

      expect(await screen.findByText('Your list is empty')).toBeInTheDocument();
      expect(JSON.parse(localStorage.getItem(LIST_KEY) ?? '[]')).toEqual([]);
    });

    it('leaves titles the user did not remove untouched', async () => {
      localStorage.setItem(LIST_KEY, JSON.stringify(['m-1', 'm-2']));

      renderWithQuery(<MyListPage />);
      await screen.findByText('Nothing in progress');
      openWatchlistTab();
      await waitFor(() => expect(watchHrefs()).toEqual(['/watch/m-1', '/watch/m-2']));

      fireEvent.click(removeButton('Static Bloom'));

      await waitFor(() => expect(watchHrefs()).toEqual(['/watch/m-1']));
      expect(JSON.parse(localStorage.getItem(LIST_KEY) ?? '[]')).toEqual(['m-1']);
    });
  });

  describe('tab counters', () => {
    it('counts in-progress titles and saved titles separately', async () => {
      apiClient.getWatchHistory.mockResolvedValue([
        historyItem(300, 1200),
        historyItem(60, 1200, BLOOM),
      ]);
      localStorage.setItem(LIST_KEY, JSON.stringify(['m-1']));

      renderWithQuery(<MyListPage />);

      expect(
        await screen.findByRole('button', { name: /Continue Watching \(2\)/ }),
      ).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /My List \(1\)/ })).toBeInTheDocument();
    });

    it('omits a zero counter so empty tabs stay quiet', async () => {
      renderWithQuery(<MyListPage />);

      await screen.findByText('Nothing in progress');
      expect(screen.getByRole('button', { name: 'Continue Watching' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'My List' })).toBeInTheDocument();
    });
  });
});
