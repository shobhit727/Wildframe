import "@testing-library/jest-dom";

// Minimal localStorage implementation for jsdom environments where the
// native storage global is not exposed (node 26 + jsdom 23 combination).
if (typeof window !== "undefined" && typeof window.localStorage === "undefined") {
  const store = new Map<string, string>();
  const storage: Storage = {
    get length() {
      return store.size;
    },
    clear: () => store.clear(),
    getItem: (key: string) => store.get(key) ?? null,
    key: (index: number) => Array.from(store.keys())[index] ?? null,
    removeItem: (key: string) => void store.delete(key),
    setItem: (key: string, value: string) => void store.set(key, String(value)),
  };
  Object.defineProperty(window, "localStorage", { value: storage, configurable: true });
  Object.defineProperty(globalThis, "localStorage", { value: storage, configurable: true });
}


// jsdom intentionally omits several browser APIs used by the real application.
// These defensive shims exercise the application branches without replacing
// implementations supplied by a future jsdom release.
if (typeof window !== "undefined") {
  if (!window.matchMedia) {
    window.matchMedia = (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }) as MediaQueryList;
  }

  if (!window.scrollTo) {
    window.scrollTo = () => {};
  }

  if (!URL.createObjectURL) {
    URL.createObjectURL = () => "blob:vitest";
  }
  if (!URL.revokeObjectURL) {
    URL.revokeObjectURL = () => {};
  }

  if (!("MediaSource" in window)) {
    (window as Window & { MediaSource?: typeof MediaSource }).MediaSource =
      class FakeMediaSource {} as typeof MediaSource;
  }
}

if (typeof Element !== "undefined" && !Element.prototype.scrollTo) {
  Element.prototype.scrollTo = () => {};
}

if (typeof ResizeObserver === "undefined") {
  class ResizeObserverShim {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  globalThis.ResizeObserver = ResizeObserverShim as typeof ResizeObserver;
}

if (typeof IntersectionObserver === "undefined") {
  class IntersectionObserverShim {
    readonly root = null;
    readonly rootMargin = "";
    readonly thresholds: readonly number[] = [0];
    constructor(_callback: IntersectionObserverCallback, _options?: IntersectionObserverInit) {}
    observe() {}
    unobserve() {}
    disconnect() {}
    takeRecords(): IntersectionObserverEntry[] { return []; }
  }
  globalThis.IntersectionObserver = IntersectionObserverShim as typeof IntersectionObserver;
}

if (typeof HTMLMediaElement !== "undefined") {
  HTMLMediaElement.prototype.play ||= (() => Promise.resolve()) as typeof HTMLMediaElement.prototype.play;
  HTMLMediaElement.prototype.pause ||= (() => {}) as typeof HTMLMediaElement.prototype.pause;
  HTMLMediaElement.prototype.load ||= (() => {}) as typeof HTMLMediaElement.prototype.load;
}

if (typeof HTMLVideoElement !== "undefined") {
  HTMLVideoElement.prototype.canPlayType ||= (() => "probably") as typeof HTMLVideoElement.prototype.canPlayType;
  HTMLVideoElement.prototype.requestFullscreen ||= (() => Promise.resolve()) as typeof HTMLVideoElement.prototype.requestFullscreen;
}

if (typeof document !== "undefined" && !document.exitFullscreen) {
  document.exitFullscreen = (() => Promise.resolve()) as typeof document.exitFullscreen;
}
