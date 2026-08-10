'use client';

import Link from 'next/link';

const FEATURES = [
  {
    eyebrow: 'EVERY SCREEN',
    title: 'Your library, wherever you are.',
    description: 'Move from your laptop to your phone or TV without losing your place. Wildframe is designed around a continuous viewing experience.',
    icon: '▣',
  },
  {
    eyebrow: 'SMART DISCOVERY',
    title: 'Find something worth watching.',
    description: 'Browse trending titles, recommendations, genres, and your personal list through a media-first interface built for exploration.',
    icon: '⌕',
  },
  {
    eyebrow: 'YOUR LIST',
    title: 'Keep the good stuff close.',
    description: 'Save titles for later and return to them when you are ready. Your watchlist stays one click away.',
    icon: '＋',
  },
];

const FAQS = [
  {
    question: 'What is Wildframe?',
    answer: 'Wildframe is a streaming platform project focused on discovery, playback, accounts, recommendations, and a modern media-library experience.',
  },
  {
    question: 'Is Wildframe production-ready?',
    answer: 'Not yet. The project is actively under development. Some CI, security, infrastructure, and product work remains before a production release should be considered.',
  },
  {
    question: 'Where can I watch?',
    answer: 'The current web application is the primary interface. Additional device and platform support can be added as the product develops.',
  },
  {
    question: 'Can I save titles?',
    answer: 'Yes. Authenticated users have a My List experience for keeping titles they want to revisit.',
  },
];

export default function HomePage() {
  return (
    <main className="wf-page overflow-hidden">
      {/* Public navigation stays intentionally minimal so the hero owns the first impression. */}
      <nav className="fixed inset-x-0 top-0 z-50 bg-gradient-to-b from-black/90 via-black/50 to-transparent">
        <div className="mx-auto flex h-20 max-w-[1800px] items-center justify-between px-5 sm:px-8 lg:px-12">
          <Link
            href="/"
            className="text-2xl font-black tracking-[-0.05em] text-[#E50914] transition-transform duration-200 hover:scale-[1.03] sm:text-3xl"
          >
            WILDFRAME
          </Link>
          <div className="flex items-center gap-3 sm:gap-5">
            <Link href="/login" className="text-sm font-medium text-gray-200 transition-colors hover:text-white">
              Sign In
            </Link>
            <Link
              href="/signup"
              className="rounded bg-[#E50914] px-4 py-2 text-sm font-semibold text-white shadow-lg shadow-red-950/30 transition-all duration-200 hover:-translate-y-0.5 hover:bg-[#f6121d]"
            >
              Get Started
            </Link>
          </div>
        </div>
      </nav>

      {/* Hero uses layered gradients instead of requiring a remote background asset. */}
      <section className="relative flex min-h-[760px] items-end overflow-hidden sm:min-h-[860px]">
        <div className="absolute inset-0 bg-[radial-gradient(circle_at_75%_30%,rgba(229,9,20,0.24),transparent_30%),radial-gradient(circle_at_20%_30%,rgba(75,60,160,0.18),transparent_32%),linear-gradient(135deg,#111_0%,#090909_48%,#170608_100%)]" />
        <div className="absolute inset-0 bg-[linear-gradient(115deg,rgba(0,0,0,.98)_0%,rgba(0,0,0,.76)_38%,rgba(0,0,0,.28)_72%,rgba(0,0,0,.78)_100%)]" />
        <div className="absolute inset-x-0 bottom-0 h-64 bg-gradient-to-t from-[#0b0b0b] to-transparent" />

        <div className="relative z-10 mx-auto w-full max-w-[1800px] px-5 pb-24 sm:px-8 sm:pb-32 lg:px-12">
          <div className="max-w-3xl animate-rise-in">
            <div className="mb-5 flex items-center gap-3 text-xs font-bold tracking-[0.25em] text-gray-300">
              <span className="h-px w-8 bg-[#E50914]" />
              STREAM SOMETHING GREAT
            </div>
            <h1 className="text-5xl font-black leading-[0.95] tracking-[-0.045em] text-white sm:text-7xl lg:text-[6.6rem]">
              Stories that pull you in.
            </h1>
            <p className="mt-7 max-w-2xl text-base leading-7 text-gray-300 sm:text-lg sm:leading-8">
              Explore movies and series through a dark, cinematic interface built around discovery, recommendations, and effortless playback.
            </p>
            <div className="mt-9 flex flex-col gap-3 sm:flex-row">
              <Link
                href="/signup"
                className="inline-flex items-center justify-center gap-2 rounded bg-[#E50914] px-7 py-3.5 text-base font-bold text-white transition-all duration-200 hover:-translate-y-0.5 hover:bg-[#f6121d] hover:shadow-[0_12px_40px_rgba(229,9,20,.28)]"
              >
                Start exploring
                <span aria-hidden="true">→</span>
              </Link>
              <Link
                href="/browse"
                className="inline-flex items-center justify-center rounded bg-white/10 px-7 py-3.5 text-base font-semibold text-white backdrop-blur-sm transition-all duration-200 hover:bg-white/15"
              >
                Browse titles
              </Link>
            </div>
          </div>
        </div>
      </section>

      {/* Feature cards create a consistent visual language for the rest of the public UI. */}
      <section className="relative border-t border-white/10 px-5 py-20 sm:px-8 lg:px-12">
        <div className="mx-auto max-w-[1500px]">
          <div className="mb-10 max-w-2xl animate-fade-in">
            <p className="text-xs font-bold tracking-[0.25em] text-[#E50914]">BUILT FOR WATCHING</p>
            <h2 className="mt-3 text-3xl font-bold tracking-tight text-white sm:text-5xl">A cleaner way to find your next watch.</h2>
          </div>

          <div className="grid gap-4 md:grid-cols-3">
            {FEATURES.map((feature, index) => (
              <article
                key={feature.title}
                className="wf-lift animate-rise-in group min-h-64 rounded-2xl border border-white/10 bg-white/[0.035] p-7 backdrop-blur-sm"
                style={{ animationDelay: `${index * 90}ms` }}
              >
                <div className="flex h-11 w-11 items-center justify-center rounded-lg bg-[#E50914]/10 text-xl text-[#ff3b44] transition-transform duration-300 group-hover:scale-110">
                  {feature.icon}
                </div>
                <p className="mt-8 text-[10px] font-bold tracking-[0.25em] text-gray-500">{feature.eyebrow}</p>
                <h3 className="mt-2 text-2xl font-bold text-white">{feature.title}</h3>
                <p className="mt-3 text-sm leading-6 text-gray-400">{feature.description}</p>
              </article>
            ))}
          </div>
        </div>
      </section>

      {/* FAQ uses native details for reliable keyboard and screen-reader behavior. */}
      <section className="border-t border-white/10 px-5 py-20 sm:px-8 lg:px-12">
        <div className="mx-auto max-w-4xl">
          <div className="mb-10 text-center">
            <p className="text-xs font-bold tracking-[0.25em] text-[#E50914]">FAQ</p>
            <h2 className="mt-3 text-3xl font-bold tracking-tight text-white sm:text-5xl">Questions, answered.</h2>
          </div>
          <div className="space-y-2">
            {FAQS.map((faq) => (
              <details key={faq.question} className="group overflow-hidden rounded-xl border border-white/10 bg-white/[0.035] transition-colors hover:bg-white/[0.055]">
                <summary className="flex cursor-pointer list-none items-center justify-between gap-6 px-5 py-5 text-base font-semibold text-white sm:px-6 sm:text-lg">
                  {faq.question}
                  <span className="text-xl font-light text-gray-400 transition-transform duration-200 group-open:rotate-45" aria-hidden="true">+</span>
                </summary>
                <div className="border-t border-white/10 px-5 pb-6 pt-4 text-sm leading-7 text-gray-400 sm:px-6">
                  {faq.answer}
                </div>
              </details>
            ))}
          </div>
        </div>
      </section>

      {/* Final call-to-action avoids the misleading membership claims in the old page. */}
      <section className="border-t border-white/10 px-5 py-24 text-center sm:px-8">
        <div className="mx-auto max-w-3xl animate-fade-in">
          <p className="text-xs font-bold tracking-[0.25em] text-[#E50914]">WILDFRAME</p>
          <h2 className="mt-4 text-4xl font-black tracking-tight text-white sm:text-6xl">Ready to explore?</h2>
          <p className="mx-auto mt-5 max-w-xl text-sm leading-7 text-gray-400 sm:text-base">
            Create an account and step into the current Wildframe experience. The platform is still being built, so expect changes as new features land.
          </p>
          <Link
            href="/signup"
            className="mt-8 inline-flex rounded bg-[#E50914] px-7 py-3.5 text-sm font-bold text-white transition-all duration-200 hover:-translate-y-0.5 hover:bg-[#f6121d]"
          >
            Create account
          </Link>
        </div>
      </section>

      <footer className="border-t border-white/10 px-5 py-10 sm:px-8 lg:px-12">
        <div className="mx-auto flex max-w-[1500px] flex-col justify-between gap-5 text-xs text-gray-500 sm:flex-row sm:items-center">
          <p>© {new Date().getFullYear()} Wildframe.</p>
          <div className="flex gap-5">
            <Link href="/login" className="transition-colors hover:text-gray-300">Sign in</Link>
            <Link href="/signup" className="transition-colors hover:text-gray-300">Create account</Link>
          </div>
        </div>
      </footer>
    </main>
  );
}
