/**
 * Creator workspace — editorial dashboard for managing a creator's Wildframe slate.
 * Creator-specific write APIs are not wired into the web client yet, so this page
 * intentionally exposes read-only platform content and clearly marks future actions.
 */
'use client';

import Link from 'next/link';
import { useQuery } from '@tanstack/react-query';
import { apiClient, normalizeContent } from '@/api/client';
import { useUser } from '@/hooks';
import { Content } from '@/types';

function Metric({ label, value, detail }: { label: string; value: string; detail: string }) {
  return (
    <article className="group rounded-2xl border border-white/10 bg-zinc-900/60 p-5 shadow-xl shadow-black/20 transition duration-300 hover:-translate-y-1 hover:border-white/15 hover:bg-zinc-900/80">
      <p className="text-xs font-medium uppercase tracking-widest text-zinc-500">{label}</p>
      <p className="mt-3 text-3xl font-bold tracking-tight text-white">{value}</p>
      <p className="mt-1 text-xs text-zinc-500">{detail}</p>
    </article>
  );
}

export default function CreatorDashboardPage() {
  const user = useUser();
  const contentQuery = useQuery({
    queryKey: ['creator', 'content'],
    queryFn: () => apiClient.getContentList({ page_size: 30 }),
    staleTime: 30_000,
  });

  const content: Content[] = (contentQuery.data ?? []).map(normalizeContent);
  const movies = content.filter((item) => item.type === 'movie').length;
  const shows = content.filter((item) => item.type === 'show').length;
  const published = content.length;

  return (
    <main className="min-h-screen bg-[#080808] px-4 pb-16 pt-24 text-white sm:px-6 lg:px-10">
      <div className="mx-auto max-w-7xl space-y-8">
        <section className="relative overflow-hidden rounded-3xl border border-white/10 bg-gradient-to-br from-zinc-900 via-zinc-950 to-red-950/30 p-6 shadow-2xl shadow-black/40 sm:p-10">
          <div aria-hidden className="pointer-events-none absolute -right-24 -top-24 h-72 w-72 rounded-full bg-red-600/15 blur-3xl" />
          <div className="relative max-w-3xl">
            <div className="mb-4 inline-flex items-center gap-2 rounded-full border border-red-500/20 bg-red-500/10 px-3 py-1 text-[10px] font-semibold uppercase tracking-[0.2em] text-red-300">
              Creator Studio
            </div>
            <h1 className="text-3xl font-bold tracking-tight sm:text-5xl">Your work, in one place.</h1>
            <p className="mt-4 max-w-2xl text-sm leading-6 text-zinc-400 sm:text-base">
              Welcome{user?.firstName ? `, ${user.firstName}` : ''}. Review the Wildframe catalog, track your slate, and prepare the workspace for publishing tools.
            </p>
            <div className="mt-7 flex flex-wrap gap-3">
              <Link href="/browse" className="rounded-lg bg-white px-4 py-2.5 text-sm font-semibold text-black transition hover:bg-zinc-200">
                View platform
              </Link>
              <button disabled className="cursor-not-allowed rounded-lg border border-white/10 bg-white/5 px-4 py-2.5 text-sm font-semibold text-zinc-500" title="Creator publishing API is not connected yet">
                Upload content — coming soon
              </button>
            </div>
          </div>
        </section>

        <section className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <Metric label="Published slate" value={contentQuery.isLoading ? '—' : String(published)} detail="titles currently visible to the web client" />
          <Metric label="Movies" value={contentQuery.isLoading ? '—' : String(movies)} detail="movie entries in the current catalog" />
          <Metric label="Series" value={contentQuery.isLoading ? '—' : String(shows)} detail="series entries in the current catalog" />
        </section>

        <section className="rounded-2xl border border-white/10 bg-zinc-900/50 p-5 shadow-xl shadow-black/20 sm:p-6">
          <div className="mb-5 flex items-end justify-between gap-4">
            <div>
              <h2 className="text-lg font-semibold">Content slate</h2>
              <p className="mt-1 text-xs text-zinc-500">A cinematic preview of the current catalog.</p>
            </div>
            <span className="text-xs text-zinc-600">Read-only preview</span>
          </div>

          {contentQuery.isLoading ? (
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
              {Array.from({ length: 10 }).map((_, index) => <div key={index} className="aspect-[2/3] animate-pulse rounded-xl bg-zinc-800/70" />)}
            </div>
          ) : contentQuery.isError ? (
            <div className="rounded-xl border border-red-500/20 bg-red-500/5 p-6 text-sm text-red-300">The catalog could not be loaded. Check the API gateway and try again.</div>
          ) : content.length === 0 ? (
            <div className="rounded-xl border border-dashed border-white/10 p-10 text-center text-sm text-zinc-500">No published content is available yet.</div>
          ) : (
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
              {content.slice(0, 10).map((item) => (
                <Link key={item.id} href={`/watch/${item.id}`} className="group relative overflow-hidden rounded-xl border border-white/10 bg-zinc-950 transition duration-300 hover:-translate-y-1 hover:border-white/20 hover:shadow-2xl hover:shadow-black/50">
                  <div className="aspect-[2/3] bg-gradient-to-br from-zinc-800 to-zinc-950">
                    {item.poster ? <img src={item.poster} alt="" className="h-full w-full object-cover transition duration-500 group-hover:scale-105" /> : null}
                    <div className="absolute inset-0 bg-gradient-to-t from-black via-black/10 to-transparent" />
                    <div className="absolute inset-x-0 bottom-0 p-3">
                      <p className="truncate text-sm font-semibold text-white">{item.title}</p>
                      <p className="mt-1 text-[10px] uppercase tracking-wider text-zinc-400">{item.type}</p>
                    </div>
                  </div>
                </Link>
              ))}
            </div>
          )}
        </section>

        <section className="grid grid-cols-1 gap-4 md:grid-cols-3">
          {[
            ['Content', 'Organize titles, metadata, artwork, and release information.'],
            ['Analytics', 'Creator-level performance reporting will connect here.'],
            ['Publishing', 'Upload, processing, and release workflows are planned for the next backend integration.'],
          ].map(([title, description]) => (
            <article key={title} className="rounded-2xl border border-white/10 bg-zinc-900/40 p-5 transition hover:bg-zinc-900/60">
              <h3 className="font-semibold text-white">{title}</h3>
              <p className="mt-2 text-sm leading-6 text-zinc-500">{description}</p>
            </article>
          ))}
        </section>
      </div>
    </main>
  );
}
