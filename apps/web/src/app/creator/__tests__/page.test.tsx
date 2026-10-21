import { beforeEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';

import CreatorDashboardPage from '@/app/creator/page';
import { useAuthStore } from '@/stores/auth';
import { renderWithQuery, makeBackendContent, makeUser } from '@/__tests__/app-helpers';

const { apiClient } = vi.hoisted(() => ({
  apiClient: { getContentList: vi.fn() },
}));

vi.mock('@/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/client')>();
  return { ...actual, apiClient };
});

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
  usePathname: () => '/creator',
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

/** Metric card <article> for a given label, so the value can be read in place. */
function metric(label: string): HTMLElement {
  return screen.getByText(label).closest('article') as HTMLElement;
}

/** Structural query: see the note in the browse page test. */
function slateLinks(): string[] {
  return Array.from(document.querySelectorAll('a[href^="/watch/"]')).map((a) =>
    a.getAttribute('href') as string,
  );
}

const MOVIE = makeBackendContent({ id: 'm-1', title: 'Neon Drift', content_type: 'movie' });
const SHOW = makeBackendContent({
  id: 's-1',
  title: 'Orbital Drift',
  content_type: 'series',
  duration_minutes: 45,
});

beforeEach(() => {
  apiClient.getContentList.mockReset().mockResolvedValue([MOVIE, SHOW]);
  useAuthStore.setState({
    user: makeUser(),
    token: 'tok',
    isAuthenticated: true,
    isLoading: false,
  });
});

describe('CreatorDashboardPage (/creator)', () => {
  it('greets the signed-in creator by first name', async () => {
    renderWithQuery(<CreatorDashboardPage />);

    expect(await screen.findByText(/Welcome, Ada/)).toBeInTheDocument();
  });

  it('greets without a name when the user object is unknown', async () => {
    useAuthStore.setState({ user: null, token: null, isAuthenticated: false });

    renderWithQuery(<CreatorDashboardPage />);

    // The dashboard is not auth-gated, so a signed-out visitor still gets a
    // coherent page rather than a broken greeting.
    expect(await screen.findByText(/^Welcome\./)).toBeInTheDocument();
    expect(screen.queryByText(/Welcome,/)).toBeNull();
  });

  it('requests thirty catalogue entries for the slate', async () => {
    renderWithQuery(<CreatorDashboardPage />);

    await waitFor(() =>
      expect(apiClient.getContentList).toHaveBeenCalledWith({ page_size: 30 }),
    );
  });

  describe('metrics', () => {
    it('shows a placeholder for every metric while the slate is loading', () => {
      apiClient.getContentList.mockReturnValue(new Promise(() => {}));

      renderWithQuery(<CreatorDashboardPage />);

      // A dash rather than a zero: reporting "0 published" before the request
      // settles would be a false statement about the catalogue.
      expect(metric('Published slate')).toHaveTextContent('—');
      expect(metric('Movies')).toHaveTextContent('—');
      expect(metric('Series')).toHaveTextContent('—');
    });

    it('counts movies and series separately from the total', async () => {
      renderWithQuery(<CreatorDashboardPage />);

      await waitFor(() => expect(metric('Published slate')).toHaveTextContent('2'));
      expect(metric('Movies')).toHaveTextContent('1');
      expect(metric('Series')).toHaveTextContent('1');
    });

    it('reports zero for every bucket when the catalogue is empty', async () => {
      apiClient.getContentList.mockResolvedValue([]);

      renderWithQuery(<CreatorDashboardPage />);

      await waitFor(() => expect(metric('Published slate')).toHaveTextContent('0'));
      expect(metric('Movies')).toHaveTextContent('0');
      expect(metric('Series')).toHaveTextContent('0');
    });

    it('normalises an unexpected content_type as a movie', async () => {
      // normalizeContent maps anything that is not series/show to "movie", so
      // the Movies counter must not silently drop these.
      apiClient.getContentList.mockResolvedValue([
        makeBackendContent({ id: 'x-1', content_type: 'episode' }),
      ]);

      renderWithQuery(<CreatorDashboardPage />);

      await waitFor(() => expect(metric('Movies')).toHaveTextContent('1'));
    });
  });

  describe('content slate', () => {
    it('links each slate entry to its watch page', async () => {
      renderWithQuery(<CreatorDashboardPage />);

      await waitFor(() => expect(slateLinks()).toEqual(['/watch/m-1', '/watch/s-1']));
    });

    it('labels each entry with its title and type', async () => {
      renderWithQuery(<CreatorDashboardPage />);

      await waitFor(() => expect(slateLinks()).toEqual(['/watch/m-1', '/watch/s-1']));

      // Located structurally: the slate renders ten poster tiles, and matching
      // one by accessible name means building the a11y tree for all of them.
      const tile = document.querySelector('a[href="/watch/m-1"]');
      expect(tile?.textContent).toContain('Neon Drift');
      expect(tile?.textContent).toContain('movie');
    });

    it('caps the preview at ten entries', async () => {
      apiClient.getContentList.mockResolvedValue(
        Array.from({ length: 14 }, (_, i) =>
          makeBackendContent({ id: `c-${i}`, title: `Title ${i}` }),
        ),
      );

      renderWithQuery(<CreatorDashboardPage />);

      await waitFor(() => expect(metric('Published slate')).toHaveTextContent('14'));
      // The counter reports everything; the preview grid stays short.
      expect(slateLinks()).toHaveLength(10);
    });

    it('says so plainly when the catalogue is empty', async () => {
      apiClient.getContentList.mockResolvedValue([]);

      renderWithQuery(<CreatorDashboardPage />);

      expect(
        await screen.findByText('No published content is available yet.'),
      ).toBeInTheDocument();
    });

    it('explains a failed catalogue load instead of showing an empty slate', async () => {
      apiClient.getContentList.mockRejectedValue(new Error('gateway 502'));

      renderWithQuery(<CreatorDashboardPage />);

      // A silent blank grid would read as "you have no content" rather than
      // "the request failed", which is a very different message for a creator.
      expect(
        await screen.findByText('The catalog could not be loaded. Check the API gateway and try again.'),
      ).toBeInTheDocument();
      expect(
        screen.queryByText('No published content is available yet.'),
      ).toBeNull();
    });

    it('keeps the metric counters at zero on a failed load', async () => {
      apiClient.getContentList.mockRejectedValue(new Error('gateway 502'));

      renderWithQuery(<CreatorDashboardPage />);

      await waitFor(() => expect(metric('Published slate')).toHaveTextContent('0'));
    });

    it('is labelled a read-only preview', async () => {
      renderWithQuery(<CreatorDashboardPage />);

      expect(await screen.findByText('Read-only preview')).toBeInTheDocument();
    });
  });

  describe('actions', () => {
    it('offers a route into the platform viewer', async () => {
      renderWithQuery(<CreatorDashboardPage />);

      expect(await screen.findByRole('link', { name: 'View platform' })).toHaveAttribute(
        'href',
        '/browse',
      );
    });

    it('disables the upload affordance until the publishing API exists', async () => {
      renderWithQuery(<CreatorDashboardPage />);

      const upload = await screen.findByRole('button', {
        name: 'Upload content — coming soon',
      });
      // A live-looking upload button that silently does nothing is worse than
      // an explicitly disabled one.
      expect(upload).toBeDisabled();
      expect(upload).toHaveAttribute(
        'title',
        'Creator publishing API is not connected yet',
      );
    });

    it('describes the three upcoming workspace areas', async () => {
      renderWithQuery(<CreatorDashboardPage />);

      expect(await screen.findByRole('heading', { name: 'Content' })).toBeInTheDocument();
      expect(screen.getByRole('heading', { name: 'Analytics' })).toBeInTheDocument();
      expect(screen.getByRole('heading', { name: 'Publishing' })).toBeInTheDocument();
      expect(screen.getByText(/will connect here/i)).toBeInTheDocument();
    });
  });
});
