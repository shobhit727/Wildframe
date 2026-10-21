import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';

import BrowsePage from '@/app/browse/page';
import { useAuthStore } from '@/stores/auth';
import { renderWithQuery, makeBackendContent, makeUser } from '@/__tests__/app-helpers';

const { apiClient } = vi.hoisted(() => ({
  apiClient: {
    getContentList: vi.fn(),
    getTrending: vi.fn(),
    getRecommendations: vi.fn(),
    searchContent: vi.fn(),
  },
}));

vi.mock('@/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/client')>();
  return { ...actual, apiClient };
});

const push = vi.fn();

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push, replace: vi.fn(), refresh: vi.fn() }),
  usePathname: () => '/browse',
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

const SCI_FI = { id: 'g-sf', name: 'sci-fi', slug: 'sci-fi' };
const DRAMA = { id: 'g-dr', name: 'drama', slug: 'drama' };

// Two sci-fi movies and one drama series. Neither genre reaches the three-item
// threshold the page uses to decide whether a genre rail is worth rendering.
const MOVIE = makeBackendContent({
  id: 'm-1',
  title: 'Neon Drift',
  content_type: 'movie',
  genres: [SCI_FI],
});
const MOVIE_2 = makeBackendContent({
  id: 'm-2',
  title: 'Static Bloom',
  content_type: 'movie',
  genres: [SCI_FI],
});
const SHOW = makeBackendContent({
  id: 's-1',
  title: 'Orbital Drift',
  content_type: 'series',
  duration_minutes: 45,
  genres: [DRAMA],
});

/**
 * Heading/link lookups on this page are structural rather than role-based.
 *
 * dom-testing-library's getByRole builds the full accessibility tree before
 * filtering, which costs ~600ms on a fully populated browse page (291 nodes,
 * eight MediaCards) versus ~1ms for querySelectorAll. That is the difference
 * between a green test and a 5s timeout, so enumeration and heading lookup go
 * through the DOM directly. Accessible queries are still used wherever the
 * target is unique and cheap (labels, buttons, links by accessible name).
 */
function headings(level: 1 | 2): string[] {
  return Array.from(document.querySelectorAll(`h${level}`)).map((h) => (h.textContent ?? '').trim());
}

/** Row <section> for a given row title, so card assertions can be scoped. */
function row(title: string): HTMLElement {
  const heading = Array.from(document.querySelectorAll('h2')).find(
    (h) => (h.textContent ?? '').trim() === title,
  );
  if (!heading) throw new Error(`row "${title}" not found`);
  return heading.closest('section') as HTMLElement;
}

/** Resolve once a row heading is on screen. */
async function waitForRow(title: string): Promise<void> {
  await waitFor(() => expect(headings(2)).toContain(title));
}

/** Resolve once an h2 with the given text is on screen. */
async function waitForHeading(text: string): Promise<void> {
  await waitFor(() => expect(headings(2)).toContain(text));
}

function watchHrefsIn(title: string): string[] {
  return Array.from(row(title).querySelectorAll('a[href^="/watch/"]')).map((a) =>
    a.getAttribute('href') as string,
  );
}

/** Every watch-page link currently on screen, in DOM order. */
function watchHrefs(): string[] {
  return Array.from(document.querySelectorAll('a[href^="/watch/"]')).map((a) =>
    a.getAttribute('href') as string,
  );
}

/** Rail titles rendered at heading level 2 (excludes the "Results for" title). */
function railTitles(): string[] {
  return headings(2);
}

/** Drive the real navbar search box, the way a visitor would. */
function search(value: string) {
  const toggle = screen.queryByLabelText('Search') ?? screen.getByLabelText('Close search');
  if (toggle.getAttribute('aria-label') === 'Search') {
    fireEvent.click(toggle);
  }
  fireEvent.change(screen.getByLabelText('Search titles, people, or genres'), {
    target: { value },
  });
}

beforeEach(() => {
  push.mockReset();
  for (const fn of Object.values(apiClient)) fn.mockReset();

  apiClient.getContentList.mockImplementation(
    async ({ content_type }: { content_type?: string } = {}) => {
      if (content_type === 'movie') return [MOVIE, MOVIE_2];
      if (content_type === 'series') return [SHOW];
      return [MOVIE, MOVIE_2, SHOW];
    }
  );
  apiClient.getTrending.mockResolvedValue([SHOW, MOVIE]);
  apiClient.getRecommendations.mockResolvedValue([]);
  apiClient.searchContent.mockResolvedValue([MOVIE]);

  useAuthStore.setState({
    user: makeUser(),
    token: 'tok',
    isAuthenticated: true,
    isLoading: false,
  });
});

describe('BrowsePage (/browse)', () => {
  describe('authentication gate', () => {
    it('redirects a signed-out visitor and never queries the catalogue', async () => {
      useAuthStore.setState({ user: null, token: null, isAuthenticated: false });

      renderWithQuery(<BrowsePage />);

      expect(await screen.findByText('Loading...')).toBeInTheDocument();
      expect(push).toHaveBeenCalledWith('/login');
      // Firing catalogue requests before the guard resolves would leak
      // un-authenticated 401 noise into the gateway logs on every page load.
      expect(apiClient.getContentList).not.toHaveBeenCalled();
      expect(apiClient.getTrending).not.toHaveBeenCalled();
    });

    it('hides the browse rows while the session is unknown', async () => {
      useAuthStore.setState({ user: null, token: null, isAuthenticated: false });

      renderWithQuery(<BrowsePage />);

      await screen.findByText('Loading...');
      expect(railTitles()).not.toContain('Popular Movies');
    });
  });

  describe('catalogue rows', () => {
    it('requests movies and series separately, capped at 30 each', async () => {
      renderWithQuery(<BrowsePage />);

      await waitFor(() => expect(apiClient.getContentList).toHaveBeenCalled());
      expect(apiClient.getContentList).toHaveBeenCalledWith({
        content_type: 'movie',
        page_size: 30,
      });
      expect(apiClient.getContentList).toHaveBeenCalledWith({
        content_type: 'series',
        page_size: 30,
      });
    });

    it('renders a row per catalogue section with links to the watch page', async () => {
      renderWithQuery(<BrowsePage />);

      await waitForRow('Popular Movies');
      expect(railTitles()).toContain('TV Shows');
      expect(railTitles()).toContain('Trending Now');

      expect(watchHrefsIn('Popular Movies')).toEqual(['/watch/m-1', '/watch/m-2']);
      expect(watchHrefsIn('TV Shows')).toEqual(['/watch/s-1']);
    });

    it('features the first trending title in the billboard hero', async () => {
      renderWithQuery(<BrowsePage />);

      await waitFor(() => expect(headings(1)).toContain('Orbital Drift'));
    });

    it('omits the recommendations row when the service returns nothing', async () => {
      renderWithQuery(<BrowsePage />);

      await waitForRow('Popular Movies');
      // An empty "Recommended For You" rail would render as a stranded heading
      // with nothing under it.
      expect(railTitles()).not.toContain('Recommended For You');
    });

    it('renders the recommendations row when personalised titles exist', async () => {
      apiClient.getRecommendations.mockResolvedValue([
        makeBackendContent({ id: 'r-1', title: 'Personal Pick' }),
      ]);

      renderWithQuery(<BrowsePage />);

      await waitForRow('Recommended For You');
      expect(apiClient.getRecommendations).toHaveBeenCalledWith('u-1', 20);
    });

    it('drops genre rails that do not have at least three titles', async () => {
      // Two sci-fi films and one drama series: a 1- or 2-card "rail" is a row of
      // dead space that duplicates the Popular Movies / TV Shows rows.
      renderWithQuery(<BrowsePage />);

      await waitForRow('Popular Movies');

      expect(railTitles()).not.toContain('sci-fi');
      expect(railTitles()).not.toContain('drama');
    });

    it('promotes a genre to its own rail once it has three titles', async () => {
      apiClient.getContentList.mockImplementation(
        async ({ content_type }: { content_type?: string } = {}) => {
          if (content_type === 'series') {
            return [
              SHOW,
              makeBackendContent({ id: 's-2', title: 'Quiet Orbit', content_type: 'series', genres: [DRAMA] }),
              makeBackendContent({ id: 's-3', title: 'Hard Orbit', content_type: 'series', genres: [DRAMA] }),
            ];
          }
          return [MOVIE, MOVIE_2];
        }
      );

      renderWithQuery(<BrowsePage />);

      // Three drama series clears the threshold and the rail must appear.
      await waitForRow('drama');
      expect(watchHrefsIn('drama')).toEqual(['/watch/s-1', '/watch/s-2', '/watch/s-3']);
      // sci-fi still has only two, so it must stay out.
      expect(railTitles()).not.toContain('sci-fi');
    });
  });

  describe('search', () => {
    it('ignores a single character query and keeps the browse view', async () => {
      renderWithQuery(<BrowsePage />);
      await waitForRow('Popular Movies');

      search('m');

      expect(headings(2).some((t) => /results for/i.test(t))).toBe(false);
      expect(railTitles()).toContain('Popular Movies');
      expect(apiClient.searchContent).not.toHaveBeenCalled();
    });

    it('switches to a results view and queries the search service', async () => {
      renderWithQuery(<BrowsePage />);
      await waitForRow('Popular Movies');

      search('neo');

      await waitForHeading('Results for “neo”');
      await waitFor(() => expect(apiClient.searchContent).toHaveBeenCalledWith('neo'));
    });

    it('replaces the browse rails with the result grid', async () => {
      renderWithQuery(<BrowsePage />);
      await waitForRow('Popular Movies');

      search('neo');

      // The "Results for" heading renders before the request settles, so wait
      // for the cards themselves rather than the heading.
      await waitFor(() => expect(watchHrefs()).toContain('/watch/m-1'));
      expect(railTitles()).not.toContain('Popular Movies');
      expect(railTitles()).not.toContain('Trending Now');
      expect(railTitles()).not.toContain('TV Shows');
    });

    it('shows an explicit empty state when nothing matches', async () => {
      apiClient.searchContent.mockResolvedValue([]);

      renderWithQuery(<BrowsePage />);
      await waitForRow('Popular Movies');

      search('zzzz');

      expect(await screen.findByText('No results found')).toBeInTheDocument();
      expect(screen.getByText('Try searching for a different title.')).toBeInTheDocument();
    });
  });

  describe('failure paths', () => {
    it('renders no rails rather than crashing when the catalogue request fails', async () => {
      apiClient.getContentList.mockRejectedValue(new Error('gateway 502'));
      apiClient.getTrending.mockRejectedValue(new Error('gateway 502'));

      renderWithQuery(<BrowsePage />);

      // Row and HeroBanner both bail on an empty item list, so a failed fetch
      // must degrade to a blank shell rather than an unhandled rejection.
      await waitFor(() => expect(apiClient.getContentList).toHaveBeenCalled());
      expect(railTitles()).not.toContain('Popular Movies');
      expect(headings(1)).toEqual([]);
      expect(document.querySelector('a[href="/my-list"]')).not.toBeNull();
    });

    it('keeps the results view usable when the search request fails', async () => {
      apiClient.searchContent.mockRejectedValue(new Error('search 500'));

      renderWithQuery(<BrowsePage />);
      await waitForRow('Popular Movies');

      search('neo');

      await waitForHeading('Results for “neo”');
      expect(await screen.findByText('No results found')).toBeInTheDocument();
    });
  });
});
