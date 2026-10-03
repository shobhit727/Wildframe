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

// ---------------------------------------------------------------------------
// Browser API polyfills
// ---------------------------------------------------------------------------
// jsdom 23 does not implement these. Without them, components that touch them
// either crash or silently take a fallback branch, which would make coverage
// numbers lie. Each is installed defensively so a future jsdom that ships a
// real implementation keeps it.

if (typeof window !== "undefined" && typeof window.matchMedia !== "function") {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    configurable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }),
  });
}

for (const name of ["ResizeObserver", "IntersectionObserver"] as const) {
  const g = globalThis as unknown as Record<string, unknown>;
  if (typeof g[name] !== "function") {
    g[name] = class {
      observe() {}
      unobserve() {}
      disconnect() {}
      takeRecords() {
        return [];
      }
    };
  }
}

if (typeof Element !== "undefined" && !Element.prototype.scrollTo) {
  Element.prototype.scrollTo = function scrollTo() {};
}
if (typeof window !== "undefined" && !window.scrollTo) {
  window.scrollTo = (() => {}) as typeof window.scrollTo;
}

if (typeof URL !== "undefined" && !URL.createObjectURL) {
  URL.createObjectURL = () => "blob:mock";
  URL.revokeObjectURL = () => {};
}

// HTMLMediaElement.play() returns undefined in jsdom, so `video.play().catch()`
// throws a TypeError. VideoPlayer hits this on every play.
if (typeof HTMLMediaElement !== "undefined") {
  const proto = HTMLMediaElement.prototype as unknown as Record<string, unknown>;
  if (typeof proto.play !== "function" || proto.play.length === 0) {
    Object.defineProperty(proto, "play", {
      configurable: true,
      writable: true,
      value: () => Promise.resolve(undefined),
    });
  }
  if (typeof proto.pause !== "function") {
    Object.defineProperty(proto, "pause", { configurable: true, writable: true, value: () => {} });
  }
  if (typeof proto.load !== "function") {
    Object.defineProperty(proto, "load", { configurable: true, writable: true, value: () => {} });
  }
}

if (typeof HTMLVideoElement !== "undefined") {
  const vproto = HTMLVideoElement.prototype as unknown as Record<string, unknown>;
  if (typeof vproto.requestFullscreen !== "function") {
    Object.defineProperty(vproto, "requestFullscreen", {
      configurable: true,
      writable: true,
      value: () => Promise.resolve(undefined),
    });
  }
}

if (typeof document !== "undefined" && typeof document.exitFullscreen !== "function") {
  Object.defineProperty(document, "exitFullscreen", {
    configurable: true,
    writable: true,
    value: () => Promise.resolve(undefined),
  });
}
