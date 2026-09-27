/**
 * Tests for the custom video player: stream attachment (hls.js / dash.js /
 * native fallback), the custom control surface, and the failure/retry path.
 *
 * hls.js and dash.js are mocked at the module boundary so the *branching* the
 * component performs (which engine, native fallback, teardown) is observable
 * without a real media pipeline. The `play/pause/requestFullscreen` polyfills
 * from `vitest.setup.ts` are used; jsdom's own `pause()` raises
 * "not implemented", so it is always spied rather than allowed to run.
 *
 * `HTMLMediaElement.duration` is not settable in jsdom, so duration-dependent
 * branches (progress bar width, seek target, total-time label) are out of scope
 * by design and are not simulated.
 */
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const apiMocks = vi.hoisted(() => ({
  getManifestForEpisode: vi.fn(),
  updatePlaybackPosition: vi.fn(),
  endPlaybackSession: vi.fn(),
}));

const hls = vi.hoisted(() => ({
  isSupported: vi.fn(),
  instances: [] as Array<{
    config: unknown;
    loadSource: ReturnType<typeof vi.fn>;
    attachMedia: ReturnType<typeof vi.fn>;
    destroy: ReturnType<typeof vi.fn>;
    on: ReturnType<typeof vi.fn>;
  }>,
}));

const dash = vi.hoisted(() => ({
  initialize: vi.fn(),
  destroy: vi.fn(),
  create: vi.fn(),
}));

vi.mock('@/api/client', () => ({ apiClient: apiMocks }));

vi.mock('hls.js', () => {
  class HlsMock {
    static isSupported = hls.isSupported;
    static Events = { ERROR: 'hlsEngineError' };
    config: unknown;
    loadSource = vi.fn();
    attachMedia = vi.fn();
    destroy = vi.fn();
    on = vi.fn();
    constructor(config: unknown) {
      this.config = config;
      hls.instances.push(this);
    }
  }
  return { default: HlsMock };
});

vi.mock('dashjs', () => ({
  MediaPlayer: () => ({ create: dash.create }),
}));

import { VideoPlayer } from '@/components/player/VideoPlayer';

const HLS_MANIFEST = 'https://cdn.example.test/master.m3u8';
const MP4 = 'https://cdn.example.test/clip.mp4';

type PlayerProps = Parameters<typeof VideoPlayer>[0];

let lastContainer: HTMLElement = document.body;

function renderPlayer(props: Partial<PlayerProps> = {}) {
  const result = render(<VideoPlayer contentId="c1" sessionId="s1" src={MP4} srcType="mp4" {...props} />);
  lastContainer = result.container;
  return result;
}

/** The positioned wrapper that owns the mouse handlers. */
function playerRoot(): HTMLElement {
  return lastContainer.firstElementChild as HTMLElement;
}

/** The clickable track that wraps the `role="slider"` progress bar. */
function progressTrack(): HTMLElement {
  return screen.getByRole('slider', { name: 'Playback progress' }).parentElement as HTMLElement;
}

/** The media element. Queried structurally — the component exposes no test id. */
function video(): HTMLVideoElement {
  const el = document.querySelector('video');
  if (!el) throw new Error('no <video> rendered');
  return el;
}

/** Drive `currentTime` and dispatch the React timeupdate handler. */
function emitTimeUpdate(seconds: number) {
  const el = video();
  el.currentTime = seconds;
  fireEvent.timeUpdate(el);
}

function fatalEngineError() {
  const instance = hls.instances[hls.instances.length - 1];
  const call = instance.on.mock.calls.find(([name]) => name === 'hlsEngineError');
  if (!call) throw new Error('engine error handler was never registered');
  return call[1] as (event: unknown, data: { fatal: boolean }) => void;
}

beforeEach(() => {
  hls.instances.length = 0;
  hls.isSupported.mockReset();
  hls.isSupported.mockReturnValue(true);
  apiMocks.getManifestForEpisode.mockReset();
  apiMocks.updatePlaybackPosition.mockReset();
  apiMocks.endPlaybackSession.mockReset();
  apiMocks.updatePlaybackPosition.mockResolvedValue(undefined);
  apiMocks.endPlaybackSession.mockResolvedValue(undefined);
  apiMocks.getManifestForEpisode.mockResolvedValue({ manifest_url: HLS_MANIFEST, protocol: 'hls' });
  dash.create.mockReset();
  dash.initialize.mockReset();
  dash.destroy.mockReset();
  dash.create.mockReturnValue({ initialize: dash.initialize, destroy: dash.destroy });
  // jsdom's own pause() raises "not implemented"; replace it so the component's
  // pause path is exercisable and observable.
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {});
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('stream attachment — hls.js', () => {
  it('loads an explicit hls src through hls.js without asking the backend', async () => {
    renderPlayer({ src: HLS_MANIFEST, srcType: 'hls' });

    await waitFor(() => expect(hls.instances).toHaveLength(1));
    const instance = hls.instances[0];

    expect(instance.loadSource).toHaveBeenCalledWith(HLS_MANIFEST);
    expect(instance.attachMedia).toHaveBeenCalledWith(video());
    expect(apiMocks.getManifestForEpisode).not.toHaveBeenCalled();
  });

  it('requests the manifest for the content id when no src is supplied', async () => {
    renderPlayer({ src: undefined, srcType: 'hls' });

    await waitFor(() => expect(hls.instances).toHaveLength(1));
    expect(apiMocks.getManifestForEpisode).toHaveBeenCalledWith('c1');
    expect(hls.instances[0].loadSource).toHaveBeenCalledWith(HLS_MANIFEST);
  });

  it('registers a handler for the engine ERROR event', async () => {
    renderPlayer({ src: HLS_MANIFEST, srcType: 'hls' });

    await waitFor(() => expect(hls.instances).toHaveLength(1));
    expect(hls.instances[0].on.mock.calls.some(([name]) => name === 'hlsEngineError')).toBe(true);
  });

  it('shows the retry overlay on a fatal engine error', async () => {
    renderPlayer({ src: HLS_MANIFEST, srcType: 'hls' });
    await waitFor(() => expect(hls.instances).toHaveLength(1));

    fatalEngineError()({}, { fatal: true });

    expect(await screen.findByRole('alert')).toHaveTextContent('Unable to play video');
  });

  it('ignores non-fatal engine errors', async () => {
    renderPlayer({ src: HLS_MANIFEST, srcType: 'hls' });
    await waitFor(() => expect(hls.instances).toHaveLength(1));

    fatalEngineError()({}, { fatal: false });

    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('retries from the overlay, clearing the error and rebuilding the engine', async () => {
    renderPlayer({ src: HLS_MANIFEST, srcType: 'hls' });
    await waitFor(() => expect(hls.instances).toHaveLength(1));
    fatalEngineError()({}, { fatal: true });
    expect(await screen.findByRole('alert')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));

    await waitFor(() => expect(hls.instances).toHaveLength(2));
    expect(screen.queryByRole('alert')).toBeNull();
    // The stale engine is destroyed before the replacement is constructed.
    expect(hls.instances[0].destroy).toHaveBeenCalled();
  });
});

describe('stream attachment — native HLS fallback', () => {
  beforeEach(() => {
    hls.isSupported.mockReturnValue(false);
  });

  it('sets video.src directly when the browser can play HLS natively (Safari)', async () => {
    const canPlay = vi.spyOn(HTMLVideoElement.prototype, 'canPlayType').mockReturnValue('maybe');

    renderPlayer({ src: HLS_MANIFEST, srcType: 'hls' });

    await waitFor(() => expect(canPlay).toHaveBeenCalledWith('application/vnd.apple.mpegurl'));
    await waitFor(() => expect(video().getAttribute('src')).toBe(HLS_MANIFEST));
    expect(hls.instances).toHaveLength(0);
  });

  it('leaves the element empty and reports no error when neither engine can play HLS', async () => {
    const canPlay = vi.spyOn(HTMLVideoElement.prototype, 'canPlayType').mockReturnValue('');

    renderPlayer({ src: HLS_MANIFEST, srcType: 'hls' });

    await waitFor(() => expect(canPlay).toHaveBeenCalled());
    expect(screen.queryByRole('alert')).toBeNull();
    expect(video().getAttribute('src')).toBeNull();
    expect(hls.instances).toHaveLength(0);
  });
});

describe('stream attachment — dash.js and progressive mp4', () => {
  it('initialises dash.js for a dash manifest', async () => {
    apiMocks.getManifestForEpisode.mockResolvedValue({
      manifest_url: 'https://cdn.example.test/manifest.mpd',
      protocol: 'dash',
    });

    renderPlayer({ src: undefined });

    await waitFor(() => expect(dash.initialize).toHaveBeenCalled());
    expect(dash.initialize).toHaveBeenCalledWith(video(), 'https://cdn.example.test/manifest.mpd', false);
    expect(hls.instances).toHaveLength(0);
  });

  it('treats an unknown manifest protocol as hls rather than dash', async () => {
    apiMocks.getManifestForEpisode.mockResolvedValue({ manifest_url: HLS_MANIFEST, protocol: 'progressive' });

    renderPlayer({ src: undefined });

    await waitFor(() => expect(hls.instances).toHaveLength(1));
    expect(dash.initialize).not.toHaveBeenCalled();
  });

  it('points the element straight at an mp4 source', async () => {
    renderPlayer({ src: MP4, srcType: 'mp4' });

    await waitFor(() => expect(video().getAttribute('src')).toBe(MP4));
    expect(hls.instances).toHaveLength(0);
    expect(dash.initialize).not.toHaveBeenCalled();
  });
});

describe('manifest failure handling', () => {
  it('shows the retry overlay when the manifest request rejects', async () => {
    apiMocks.getManifestForEpisode.mockRejectedValue(new Error('offline'));

    renderPlayer({ src: undefined });

    expect(await screen.findByRole('alert')).toHaveTextContent('Unable to play video');
    expect(hls.instances).toHaveLength(0);
  });

  it('shows the retry overlay when the media element fires an error event', async () => {
    renderPlayer();

    fireEvent.error(video());

    expect(await screen.findByRole('alert')).toBeInTheDocument();
  });

  it('shows no error when the manifest resolves without a playable url', async () => {
    apiMocks.getManifestForEpisode.mockResolvedValue(null);

    renderPlayer({ src: undefined });

    await waitFor(() => expect(apiMocks.getManifestForEpisode).toHaveBeenCalled());
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('stays silent when the player unmounts before the manifest resolves', async () => {
    let release: (value: unknown) => void = () => {};
    apiMocks.getManifestForEpisode.mockReturnValue(
      new Promise((resolve) => {
        release = resolve;
      })
    );

    const { unmount } = renderPlayer({ src: undefined });
    await waitFor(() => expect(apiMocks.getManifestForEpisode).toHaveBeenCalled());
    unmount();

    await act(async () => {
      release(null);
      await Promise.resolve();
    });

    // The cancelled guard must suppress both engine construction and the retry
    // overlay, otherwise React updates state after unmount.
    expect(screen.queryByRole('alert')).toBeNull();
    expect(hls.instances).toHaveLength(0);
  });

  it('destroys the engine on unmount', async () => {
    const { unmount } = renderPlayer({ src: HLS_MANIFEST, srcType: 'hls' });

    await waitFor(() => expect(hls.instances).toHaveLength(1));
    unmount();
    expect(hls.instances[0].destroy).toHaveBeenCalled();
  });
});

describe('playback controls', () => {
  it('starts and stops the media element and keeps the label in sync', async () => {
    const play = vi.spyOn(HTMLMediaElement.prototype, 'play');
    renderPlayer();

    expect(screen.getByRole('button', { name: 'Play' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Play' }));
    expect(play).toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Pause' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Pause' }));
    expect(HTMLMediaElement.prototype.pause).toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Play' })).toBeInTheDocument();
  });

  it('formats elapsed time as h:mm:ss past the one-hour mark', async () => {
    renderPlayer();

    emitTimeUpdate(3661);

    // Under an hour it reads m:ss; past an hour the hours segment must appear,
    // otherwise a 2-hour film shows a clock that restarts at 0:00.
    expect(screen.getByText(/^1:01:01 \//)).toBeInTheDocument();
  });

  it('zero-pads minutes and seconds in the elapsed time', async () => {
    renderPlayer();

    emitTimeUpdate(65);

    expect(screen.getByText(/^1:05 \//)).toBeInTheDocument();
  });

  it('reflects media events fired by the engine, not just clicks', async () => {
    renderPlayer();

    fireEvent.play(video());
    expect(screen.getByRole('button', { name: 'Pause' })).toBeInTheDocument();

    fireEvent.pause(video());
    expect(screen.getByRole('button', { name: 'Play' })).toBeInTheDocument();
  });

  it('toggles element mute state and the accessible label', async () => {
    renderPlayer();
    const el = video();

    expect(el.muted).toBe(false);
    expect(screen.getByRole('button', { name: 'Mute' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Mute' }));
    expect(el.muted).toBe(true);
    expect(screen.getByRole('button', { name: 'Unmute' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Unmute' }));
    expect(el.muted).toBe(false);
    expect(screen.getByRole('button', { name: 'Mute' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Mute' }));
    fireEvent.click(screen.getByRole('button', { name: 'Unmute' }));
    expect(el.muted).toBe(false);
    expect(screen.getByRole('button', { name: 'Mute' })).toBeInTheDocument();
  });

  it('applies the dragged volume to the element', async () => {
    renderPlayer();

    fireEvent.change(screen.getByRole('slider', { name: 'Volume' }), { target: { value: '0.4' } });

    expect(video().volume).toBeCloseTo(0.4);
    expect((screen.getByRole('slider', { name: 'Volume' }) as HTMLInputElement).value).toBe('0.4');
  });

  it('treats a dragged volume of 0 as muted', async () => {
    renderPlayer();

    fireEvent.change(screen.getByRole('slider', { name: 'Volume' }), { target: { value: '0' } });

    expect(screen.getByRole('button', { name: 'Unmute' })).toBeInTheDocument();
    expect((screen.getByRole('slider', { name: 'Volume' }) as HTMLInputElement).value).toBe('0');
  });

  it('unmutes the element when the volume slider is raised while muted', async () => {
    // Moving the slider above zero is an explicit request for audible playback.
    renderPlayer();

    fireEvent.click(screen.getByRole('button', { name: 'Mute' }));
    expect(video().muted).toBe(true);

    fireEvent.change(screen.getByRole('slider', { name: 'Volume' }), { target: { value: '0.8' } });

    expect(video().volume).toBeCloseTo(0.8);
    expect(video().muted).toBe(false);
    expect(screen.getByRole('button', { name: 'Mute' })).toBeInTheDocument();
  });

  it('restores the last nonzero volume when unmuting from zero', async () => {
    // A zero-volume slider state is still silent after unmuting unless a usable
    // level is restored from the last nonzero volume.
    renderPlayer();

    fireEvent.change(screen.getByRole('slider', { name: 'Volume' }), { target: { value: '0.6' } });
    fireEvent.change(screen.getByRole('slider', { name: 'Volume' }), { target: { value: '0' } });

    expect(video().volume).toBe(0);
    expect(video().muted).toBe(true);

    fireEvent.click(screen.getByRole('button', { name: 'Unmute' }));

    expect(video().volume).toBeCloseTo(0.6);
    expect(video().muted).toBe(false);
    expect((screen.getByRole('slider', { name: 'Volume' }) as HTMLInputElement).value).toBe('0.6');
    expect(screen.getByRole('button', { name: 'Mute' })).toBeInTheDocument();
  });

  it('requests fullscreen and flips the label, then exits on the second press', async () => {
    const request = vi
      .spyOn(HTMLVideoElement.prototype, 'requestFullscreen')
      .mockResolvedValue(undefined as never);
    const exit = vi.spyOn(document, 'exitFullscreen').mockResolvedValue(undefined as never);

    renderPlayer();

    fireEvent.click(screen.getByRole('button', { name: 'Enter fullscreen' }));
    await waitFor(() => expect(request).toHaveBeenCalled());
    expect(screen.getByRole('button', { name: 'Exit fullscreen' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Exit fullscreen' }));
    await waitFor(() => expect(exit).toHaveBeenCalled());
    expect(screen.getByRole('button', { name: 'Enter fullscreen' })).toBeInTheDocument();
  });

  it('still flips the label when the fullscreen request is rejected', async () => {
    vi.spyOn(HTMLVideoElement.prototype, 'requestFullscreen').mockRejectedValue(new Error('denied'));

    renderPlayer();
    fireEvent.click(screen.getByRole('button', { name: 'Enter fullscreen' }));

    await waitFor(() => expect(screen.getByRole('button', { name: 'Exit fullscreen' })).toBeInTheDocument());
  });
});

describe('quality selection', () => {
  it('rebuilds the streaming engine without applying the chosen quality', async () => {
    // BUG (VideoPlayer.tsx:120): `quality` is a dependency of the init effect
    // but is never handed to hls.js (no currentLevel / levels switch), so
    // picking a quality only destroys the engine and re-buffers from zero.
    renderPlayer({ src: HLS_MANIFEST, srcType: 'hls' });
    await waitFor(() => expect(hls.instances).toHaveLength(1));

    fireEvent.change(screen.getByRole('combobox', { name: 'Video quality' }), { target: { value: '720p' } });

    await waitFor(() => expect(hls.instances).toHaveLength(2));
    expect(hls.instances[0].destroy).toHaveBeenCalled();
    expect(hls.instances[1].config).toEqual({ enableWorker: true, lowLatencyMode: false });
    expect((screen.getByRole('combobox', { name: 'Video quality' }) as HTMLSelectElement).value).toBe('720p');
  });
});

describe('playback progress reporting', () => {
  it('does not persist progress before the first 30s boundary', async () => {
    renderPlayer();

    emitTimeUpdate(0);
    emitTimeUpdate(12);
    emitTimeUpdate(29);

    expect(apiMocks.updatePlaybackPosition).not.toHaveBeenCalled();
  });

  it('persists the raw position on a 30s boundary', async () => {
    renderPlayer();

    emitTimeUpdate(30.4);

    expect(apiMocks.updatePlaybackPosition).toHaveBeenCalledWith('s1', 30.4);
  });

  it('persists again at the 60s boundary but not in between', async () => {
    renderPlayer();

    emitTimeUpdate(30);
    apiMocks.updatePlaybackPosition.mockClear();

    emitTimeUpdate(59);
    expect(apiMocks.updatePlaybackPosition).not.toHaveBeenCalled();

    emitTimeUpdate(60);
    expect(apiMocks.updatePlaybackPosition).toHaveBeenCalledWith('s1', 60);
  });

  it('swallows a failed progress PATCH so playback is not interrupted', async () => {
    apiMocks.updatePlaybackPosition.mockRejectedValue(new Error('gateway down'));
    renderPlayer();

    expect(() => emitTimeUpdate(30)).not.toThrow();

    await waitFor(() => expect(apiMocks.updatePlaybackPosition).toHaveBeenCalled());
    expect(screen.queryByRole('alert')).toBeNull();
    expect(screen.getByRole('button', { name: 'Play' })).toBeInTheDocument();
  });

  it('fires one PATCH per target second despite repeated timeupdate events', async () => {
    // Regression for #946: timeupdate can fire several times inside the same
    // second while buffering or scrubbing, but that second needs only one PATCH.
    renderPlayer();

    emitTimeUpdate(30);
    emitTimeUpdate(30);
    emitTimeUpdate(30.9);

    expect(apiMocks.updatePlaybackPosition).toHaveBeenCalledTimes(1);
    expect(apiMocks.updatePlaybackPosition).toHaveBeenCalledWith('s1', 30);
  });

  it('does not re-persist a target second after scrubbing back into it', async () => {
    // The deduplication must survive backward seeks so a boundary cannot be
    // replayed by repeated scrub/timeupdate events.
    renderPlayer();

    emitTimeUpdate(30.8);
    emitTimeUpdate(60);
    emitTimeUpdate(30.2);
    emitTimeUpdate(30.7);

    expect(apiMocks.updatePlaybackPosition).toHaveBeenCalledTimes(2);
    expect(apiMocks.updatePlaybackPosition).toHaveBeenNthCalledWith(1, 's1', 30.8);
    expect(apiMocks.updatePlaybackPosition).toHaveBeenNthCalledWith(2, 's1', 60);
  });
});

describe('playback end', () => {
  it('closes the session and notifies the parent when the media ends', async () => {
    const onEnded = vi.fn();
    renderPlayer({ onEnded });

    fireEvent(video(), new Event('ended'));

    await waitFor(() => expect(apiMocks.endPlaybackSession).toHaveBeenCalledWith('s1'));
    expect(onEnded).toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Play' })).toBeInTheDocument();
  });

  it('still calls onEnded when closing the session fails', async () => {
    apiMocks.endPlaybackSession.mockRejectedValue(new Error('session already gone'));
    const onEnded = vi.fn();
    renderPlayer({ onEnded });

    fireEvent(video(), new Event('ended'));

    await waitFor(() => expect(onEnded).toHaveBeenCalled());
  });
});

describe('accessible naming', () => {
  it('exposes every custom control by an accessible name', () => {
    renderPlayer({ title: 'Neon Drift' });

    expect(screen.getByRole('slider', { name: 'Playback progress' })).toBeInTheDocument();
    expect(screen.getByRole('slider', { name: 'Volume' })).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: 'Video quality' })).toHaveValue('auto');
    expect(screen.getByRole('button', { name: 'Play' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Mute' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Enter fullscreen' })).toBeInTheDocument();
  });

  it('offers the documented quality ladder', () => {
    renderPlayer();

    const select = screen.getByRole('combobox', { name: 'Video quality' }) as HTMLSelectElement;
    const options = Array.from(select.options, (o) => o.value);

    expect(options).toEqual(['auto', '1080p', '720p', '480p']);
  });

  it('renders the poster fallback behind the stream for the given title', () => {
    const { container } = renderPlayer({ title: 'Neon Drift' });
    // PosterArt renders a generated SVG backdrop; assert the seam, not pixels.
    expect(container.querySelectorAll('svg').length).toBeGreaterThan(0);
  });
});

describe('control auto-hide', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it('hides the controls 2.5s after the mouse settles while playing', async () => {
    vi.useFakeTimers();
    renderPlayer();
    await act(async () => {
      await Promise.resolve();
    });

    fireEvent.play(video());
    expect(screen.getByRole('button', { name: 'Pause' })).toBeInTheDocument();

    fireEvent.mouseMove(playerRoot());
    act(() => {
      vi.advanceTimersByTime(2500);
    });

    // A stream with no OS cursor overlay needs its controls back; moving the
    // mouse must restore them.
    expect(screen.queryByRole('button', { name: 'Pause' })).toBeNull();

    fireEvent.mouseMove(playerRoot());
    expect(screen.getByRole('button', { name: 'Pause' })).toBeInTheDocument();
  });

  it('keeps the controls visible indefinitely while paused', async () => {
    vi.useFakeTimers();
    renderPlayer();
    await act(async () => {
      await Promise.resolve();
    });

    fireEvent.mouseMove(playerRoot());
    act(() => {
      vi.advanceTimersByTime(10_000);
    });

    expect(screen.getByRole('button', { name: 'Play' })).toBeInTheDocument();
  });

  it('hides the controls 1.5s after the mouse leaves even while paused', async () => {
    // BUG (VideoPlayer.tsx:200-203): the leave handler schedules the hide
    // unconditionally, while `handleMouseMove` (L40) bails out when paused.
    // A paused video therefore loses its Play button 1.5s after the pointer
    // leaves, and the only way back is to jiggle the mouse — unusable with a
    // keyboard or on a touch screen, where no mouseleave ever fires.
    vi.useFakeTimers();
    renderPlayer();
    await act(async () => {
      await Promise.resolve();
    });

    fireEvent.mouseOut(playerRoot(), { relatedTarget: document.body });
    act(() => {
      vi.advanceTimersByTime(1500);
    });

    expect(screen.queryByRole('button', { name: 'Play' })).toBeNull();
  });
});

describe('progress bar seeking', () => {
  it('maps a click on the track onto the media element current time', async () => {
    renderPlayer();
    const track = progressTrack();

    // jsdom reports a zero-sized rect, so give the track a real geometry and
    // assert the handler ran. `duration` is not settable in jsdom, so the
    // computed target is 0 — the point of the test is the wiring, not the maths.
    vi.spyOn(track, 'getBoundingClientRect').mockReturnValue({
      left: 0,
      width: 200,
      top: 0,
      height: 4,
      right: 200,
      bottom: 4,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    } as DOMRect);
    const el = video();
    el.currentTime = 42;

    fireEvent.click(track, { clientX: 100 });

    expect(el.currentTime).not.toBe(42);
  });
});

describe('resilience of the control handlers', () => {
  it('stays on screen when the browser refuses to start playback', async () => {
    // jsdom's play() rejects with NotSupportedError for an unplayable source.
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockRejectedValue(new Error('NotSupportedError'));
    renderPlayer();

    fireEvent.click(screen.getByRole('button', { name: 'Play' }));

    // The unhandled rejection is swallowed by `.catch(() => {})`; the control
    // still flips so the user is not left with a dead button.
    await waitFor(() => expect(screen.getByRole('button', { name: 'Pause' })).toBeInTheDocument());
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('ignores a late fatal engine error after unmount', async () => {
    const { unmount } = renderPlayer({ src: HLS_MANIFEST, srcType: 'hls' });
    await waitFor(() => expect(hls.instances).toHaveLength(1));
    const handler = fatalEngineError();

    unmount();
    handler({}, { fatal: true });

    // The cancelled guard prevents a setState on an unmounted component, which
    // React logs as an error and which would resurface as an error boundary.
    expect(screen.queryByRole('alert')).toBeNull();
  });
});
