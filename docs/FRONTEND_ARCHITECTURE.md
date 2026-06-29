# 🎨 Frontend Architecture

The Wildframe web app is a Next.js 15 application using the App Router, TypeScript, TailwindCSS, and a small set of opinionated client libraries.

**Last Updated**: June 4, 2026
**Stack**: Next.js 15 · React 19 · TypeScript 5 · TailwindCSS 4 · TanStack Query · Zustand · Axios

---

## Table of Contents

1. [Goals](#goals)
2. [Top-Level Layout](#top-level-layout)
3. [App Router Structure](#app-router-structure)
4. [State Management](#state-management)
5. [Data Fetching](#data-fetching)
6. [Styling](#styling)
7. [Auth & Sessions](#auth--sessions)
8. [Video Playback](#video-playback)
9. [Testing](#testing)
10. [Conventions](#conventions)

---

## Goals

- **Streaming-first UX** — fast browse → click → play with minimal JS on the wire.
- **Edge-rendered** — the homepage and browse pages are statically rendered or ISR-cached.
- **Strictly typed end-to-end** — the API contract is mirrored in `src/types/` and consumed everywhere.
- **No global CSS leakage** — Tailwind tokens and CSS variables only.

---

## Top-Level Layout

```
apps/web/
├── src/
│   ├── app/             # Next.js App Router (pages, layouts, route handlers)
│   ├── pages/           # Pages Router artifacts (legacy; migrate to app/)
│   ├── components/      # Reusable presentational + container components
│   ├── hooks/           # React hooks (data, UI, auth)
│   ├── stores/          # Zustand stores
│   ├── api/             # Axios client + endpoint helpers
│   ├── types/           # TypeScript types mirroring backend DTOs
│   ├── utils/           # Pure helpers (formatters, guards)
│   ├── config/          # Environment + feature flags
│   ├── constants/       # Strings, enums
│   └── middleware.ts    # Next.js middleware (auth redirect, headers)
├── public/
├── tests/               # Vitest + Playwright (planned)
├── tailwind.config.ts
├── next.config.ts
└── package.json
```

The `src/` mirror of the App Router (`pages/`, `stores/`, `api/`, etc.) holds cross-cutting code; per-route code lives under `src/app/<route>/`.

---

## App Router Structure

Route folders are organized by **user intent**, not by backend resource:

| Route | Purpose |
|---|---|
| `/` | Marketing / home (server component, ISR) |
| `/login`, `/signup` | Auth (client components) |
| `/browse` | Catalog (server component + client island) |
| `/watch/[contentId]` | Player page (client component, dynamic) |
| `/my-list` | Personal watchlist (client, auth-gated) |
| `/account` | Profile + preferences (client, auth-gated) |
| `/billing` | Subscription management (client, auth-gated) |

Each route folder can contain:

- `page.tsx` — the route entry (server by default, opt into `"use client"`)
- `layout.tsx` — shared layout for the segment
- `loading.tsx` — Suspense fallback (skeleton component)
- `error.tsx` — error boundary
- `_components/` — route-private components (underscore prefix to keep them out of routing)

### Server vs. Client Components

Default to **server components**. Use `"use client"` only when you need:

- State (`useState`, `useReducer`, Zustand)
- Effects (`useEffect`)
- Browser APIs (`window`, `localStorage`)
- Event handlers attached to the DOM

Player, login, my-list, and account pages are client. Home, browse, and watch shells are server.

---

## State Management

| Concern | Tool | Why |
|---|---|---|
| Server cache (catalog, recommendations) | **TanStack Query** | Dedupe, retries, stale-while-revalidate |
| Client UI state (modals, filters) | **`useState`** / `useReducer` | Local, ephemeral |
| Cross-route client state (auth user, theme) | **Zustand** | Tiny, no provider tree |
| Form state | **react-hook-form** + zod | Performant, typed |

### Zustand store example

```ts
// stores/useAuthStore.ts
import { create } from "zustand";
import type { User } from "@/types/user";

interface AuthState {
  user: User | null;
  setUser: (user: User | null) => void;
  logout: () => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  setUser: (user) => set({ user }),
  logout: () => set({ user: null }),
}));
```

Stores live in `src/stores/` and are pure (no side effects on import).

---

## Data Fetching

We never use `fetch` directly inside components. The `src/api/` layer wraps Axios and exposes typed functions per resource:

```ts
// api/content.ts
import { api } from "./client";
import type { Content, Page } from "@/types/content";

export async function listMovies(params: { page?: number; genre?: string }) {
  const { data } = await api.get<Page<Content>>("/content/movies", { params });
  return data;
}
```

Components use TanStack Query:

```tsx
"use client";
import { useQuery } from "@tanstack/react-query";
import { listMovies } from "@/api/content";

export function MovieGrid({ genre }: { genre?: string }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["movies", genre],
    queryFn: () => listMovies({ genre }),
  });

  if (isLoading) return <MovieGridSkeleton />;
  if (error) return <ErrorState />;
  return <Grid items={data!.items} />;
}
```

This gives us: caching, retry, devtools, and the ability to mutate the cache after writes.

---

## Styling

- **TailwindCSS 4** with CSS-variable design tokens (see `tailwind.config.ts`).
- **shadcn-style primitives** (Radix UI under the hood) in `src/components/ui/`.
- **clsx** for conditional class names.
- No inline styles, no CSS modules, no styled-components.

Theme is driven by `next-themes` and exposed via CSS variables so dark mode is a one-line toggle.

---

## Auth & Sessions

- Access tokens live in **memory** (Zustand). Never `localStorage`.
- Refresh tokens live in an **HttpOnly cookie** set by the auth service.
- `src/middleware.ts` redirects unauthenticated requests away from `/account`, `/billing`, `/my-list`, and `/watch`.
- The Axios client automatically refreshes on 401 once per request, then surfaces the error.

```ts
// middleware.ts
import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

const PROTECTED = ["/account", "/billing", "/my-list", "/watch"];

export function middleware(req: NextRequest) {
  const hasSession = req.cookies.has("wildframe_session");
  if (PROTECTED.some((p) => req.nextUrl.pathname.startsWith(p)) && !hasSession) {
    return NextResponse.redirect(new URL("/login", req.url));
  }
}
```

---

## Video Playback

The `/watch/[contentId]` page uses:

- **HLS.js** for adaptive HLS streams (default).
- **dashjs** fallback for DASH manifests when the content service returns one.
- A custom `Player` component that owns the lifecycle (load, attach source, dispose, resume position).
- Resumes from the last known position via the streaming service.

```tsx
"use client";
import Hls from "hls.js";
import { useEffect, useRef } from "react";

export function HlsPlayer({ src, onTimeUpdate }: Props) {
  const videoRef = useRef<HTMLVideoElement>(null);
  useEffect(() => {
    const hls = new Hls();
    hls.loadSource(src);
    hls.attachMedia(videoRef.current!);
    return () => hls.destroy();
  }, [src]);
  return <video ref={videoRef} onTimeUpdate={onTimeUpdate} controls />;
}
```

---

## Testing

| Layer | Tool | Where |
|---|---|---|
| Unit | **Vitest** | `tests/unit/**` |
| Component | **Vitest** + **Testing Library** | `tests/components/**` |
| E2E | **Playwright** | `tests/e2e/**` |

```bash
npm run test            # vitest
npm run test:coverage   # vitest --coverage
npm run test:e2e        # playwright test
```

Coverage target: 70%+ on `src/components/` and `src/hooks/`.

---

## Conventions

1. **Default to server components.** Add `"use client"` only when needed.
2. **All API calls go through `src/api/`.** No raw `fetch` or `axios` in components.
3. **Types live in `src/types/`** and mirror backend DTOs. Re-export from `@/types`.
4. **No barrel files inside `app/`.** They break tree-shaking.
5. **Imports use the `@/` alias.** Never long relative paths beyond one `..`.
6. **One component per file.** Co-locate tests as `Component.test.tsx`.
7. **Linting is non-negotiable.** `npm run lint` must pass before merge.

---

## Environment Variables

| Var | Purpose |
|---|---|
| `NEXT_PUBLIC_API_URL` | Backend gateway (default `http://localhost:8000`) |
| `NEXT_PUBLIC_CDN_URL` | Media CDN base |
| `WILDFRAME_SESSION_SECRET` | Cookie signing (server-only) |

---

## Related

- [ARCHITECTURE.md](ARCHITECTURE.md) — System architecture
- [SERVICE_ARCHITECTURE_PATTERN.md](SERVICE_ARCHITECTURE_PATTERN.md) — Backend layout this UI talks to
- [API_DOCUMENTATION.md](API_DOCUMENTATION.md) — Endpoints consumed by `src/api/`
- [DEVELOPMENT.md](DEVELOPMENT.md) — Day-to-day dev workflow
