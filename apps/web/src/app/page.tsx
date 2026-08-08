'use client';

import Link from 'next/link';

export default function HomePage() {
  return (
    <div className="min-h-screen bg-black">
      {/* Nav */}
      <nav className="fixed w-full top-0 z-50 bg-gradient-to-b from-black/80 to-transparent">
        <div className="max-w-6xl mx-auto px-4 py-6 flex items-center justify-between">
          <span className="text-3xl font-bold tracking-tight text-[#E50914] select-none">WILDFRAME</span>
          <div className="flex items-center gap-4">
            <Link
              href="/login"
              className="text-sm text-gray-300 hover:text-white transition-colors"
            >
              Sign In
            </Link>
            <Link
              href="/signup"
              className="bg-[#E50914] hover:bg-[#F6121D] text-white text-sm px-4 py-1.5 rounded transition-colors font-medium"
            >
              Get Started
            </Link>
          </div>
        </div>
      </nav>

      {/* Hero */}
      <section className="relative h-[85vh] flex items-center justify-center overflow-hidden">
        <div className="absolute inset-0">
          <div className="absolute inset-0 bg-gradient-to-b from-black/60 via-black/50 to-black" />
          <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_center,rgba(229,9,20,0.15),transparent_60%)]" />
        </div>

        <div className="relative max-w-3xl mx-auto px-4 text-center">
          <h1 className="text-5xl sm:text-6xl font-bold text-white leading-tight mb-6">
            Unlimited movies, TV shows &amp; more
          </h1>
          <p className="text-xl text-white mb-4">Watch anywhere. Cancel anytime.</p>
          <p className="text-lg text-gray-300 max-w-2xl mx-auto mb-8">
            Ready to watch? Enter your email to create or restart your membership.
          </p>

          <form className="flex flex-col sm:flex-row items-center justify-center gap-3 w-full max-w-xl mx-auto">
            <input
              type="email"
              placeholder="Email address"
              aria-label="Email address"
              className="flex-1 w-full bg-black/60 border border-gray-600 rounded px-4 py-3.5 text-sm text-white placeholder-gray-400 focus:outline-none focus:border-white/70"
            />
            <Link
              href="/signup"
              className="bg-[#E50914] hover:bg-[#F6121D] flex-shrink-0 w-full sm:w-auto text-white text-lg font-medium px-6 py-3 rounded transition-colors inline-flex items-center justify-center gap-2"
            >
              Get Started
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" strokeWidth={2.5} stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
              </svg>
            </Link>
          </form>
        </div>
      </section>

      {/* Feature Rows */}
      {[
        {
          title: 'Enjoy on your TV',
          desc: 'Watch on Smart TVs, Playstation, Xbox, Chromecast, Apple TV, Blu-ray players, and more.',
          emoji: '📺',
        },
        {
          title: 'Download your shows to watch offline',
          desc: 'Save your favorites easily and always have something to watch.',
          emoji: '⬇️',
        },
        {
          title: 'Watch everywhere',
          desc: 'Stream unlimited movies and TV shows on your phone, tablet, laptop, and TV.',
          emoji: '📱',
        },
      ].map((f, i) => (
        <section key={i} className={`py-16 px-4 border-t-8 border-[#222] bg-black`}>
          <div className={`max-w-6xl mx-auto flex flex-col ${i % 2 === 1 ? 'md:flex-row-reverse' : 'md:flex-row'} items-center gap-12`}>
            <div className="flex-1 text-center md:text-left">
              <h2 className="text-3xl sm:text-4xl font-bold text-white mb-4">{f.title}</h2>
              <p className="text-lg text-gray-400 leading-relaxed max-w-lg mx-auto md:mx-0">{f.desc}</p>
            </div>
            <div className="flex-1 flex justify-center">
              <div className="w-72 h-44 bg-[#1a1a1a] rounded-xl border border-[#2f2f2f] flex items-center justify-center text-6xl">
                {f.emoji}
              </div>
            </div>
          </div>
        </section>
      ))}

      {/* FAQ */}
      <section className="py-16 px-4 border-t-8 border-[#222] bg-black">
        <div className="max-w-3xl mx-auto">
          <h2 className="text-3xl sm:text-4xl font-bold text-white text-center mb-10">
            Frequently Asked Questions
          </h2>
          <div className="space-y-2">
            {[
              { q: 'What is Wildframe?', a: 'Wildframe is a streaming service that offers a wide variety of award-winning TV shows, movies, anime, documentaries, and more on thousands of internet-connected devices.' },
              { q: 'How much does Wildframe cost?', a: 'Watch Wildframe on your smartphone, tablet, Smart TV, laptop, or streaming device, all for one fixed monthly fee. Plans range from Free to $22.99 a month.' },
              { q: 'Where can I watch?', a: 'Watch anywhere, anytime. Sign in with your Wildframe account to watch instantly on the web, or on any internet-connected device.' },
              { q: 'How do I cancel?', a: 'Wildframe is flexible. There are no pesky contracts and no commitments. You can easily cancel your account online in two clicks.' },
            ].map((faq) => (
              <details key={faq.q} className="bg-[#2d2d2d] group open:bg-[#333] transition-colors">
                <summary className="px-6 py-5 text-xl text-white cursor-pointer list-none flex items-center justify-between hover:bg-[#414141] transition-colors">
                  {faq.q}
                  <svg className="w-5 h-5 text-white transition-transform group-open:rotate-45" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M12 4.5v15m7.5-7.5h-15" />
                  </svg>
                </summary>
                <div className="px-6 pb-6 text-lg text-white border-t border-black/20 pt-4 leading-relaxed">
                  {faq.a}
                </div>
              </details>
            ))}
          </div>

          <p className="text-lg text-gray-300 text-center my-8">
            Ready to watch? Enter your email to create or restart your membership.
          </p>
          <div className="flex flex-col sm:flex-row items-center justify-center gap-4 max-w-xl mx-auto">
            <input
              type="email"
              placeholder="Email address"
              aria-label="Email address"
              className="flex-1 w-full sm:w-auto bg-white/60 border border-white/40 px-4 py-3.5 text-sm text-black placeholder-gray-500 focus:outline-none"
            />
            <Link
              href="/signup"
              className="bg-[#E50914] hover:bg-[#F6121D] w-full sm:w-auto text-white text-lg font-medium px-4 py-3.5 transition-colors inline-flex items-center justify-center gap-2"
            >
              Get Started
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" strokeWidth={2.5} stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
              </svg>
            </Link>
          </div>
        </div>
      </section>

      {/* Footer */}
      <footer className="py-12 px-4 border-t-8 border-[#222] bg-black">
        <div className="max-w-6xl mx-auto">
          <p className="text-base text-gray-400 mb-8">Questions? Contact us.</p>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm text-gray-400">
            {[
              ['FAQ', 'Help Center', 'Account', 'Media Center'],
              ['Investor Relations', 'Jobs', 'Ways to Watch', 'Terms of Use'],
              ['Privacy', 'Cookie Preferences', 'Corporate Information', 'Only on Wildframe'],
            ].map((col, i) => (
              <ul key={i} className="space-y-3">
                {col.map((link) => (
                  <li key={link}>
                    <Link href="#" className="hover:underline">{link}</Link>
                  </li>
                ))}
              </ul>
            ))}
          </div>
          <p className="text-xs text-gray-500 mt-10">
            &copy; {new Date().getFullYear()} Wildframe. All rights reserved.
          </p>
        </div>
      </footer>
    </div>
  );
}