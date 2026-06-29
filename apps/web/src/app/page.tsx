'use client';

import Link from 'next/link';

export default function HomePage() {
  return (
    <div className="min-h-screen bg-dark-950">
      {/* Nav */}
      <nav className="fixed w-full top-0 z-50 bg-gradient-to-b from-black/80 to-transparent">
        <div className="max-w-6xl mx-auto px-4 py-5 flex items-center justify-between">
          <span className="text-2xl font-bold tracking-wider text-red-600">WILDFRAME</span>
          <div className="flex items-center gap-4">
            <Link
              href="/login"
              className="text-sm text-gray-300 hover:text-white transition-colors"
            >
              Sign In
            </Link>
            <Link
              href="/signup"
              className="bg-red-600 hover:bg-red-700 text-white text-sm px-5 py-2 rounded-md font-medium transition-colors"
            >
              Get Started
            </Link>
          </div>
        </div>
      </nav>

      {/* Hero */}
      <section className="relative min-h-screen flex items-center justify-center overflow-hidden">
        {/* Background Pattern */}
        <div className="absolute inset-0">
          <div className="absolute inset-0 bg-gradient-to-br from-dark-950 via-dark-900 to-dark-950" />
          <div className="absolute top-1/4 left-1/4 w-[600px] h-[600px] bg-red-600/10 blur-[120px] rounded-full" />
          <div className="absolute bottom-1/4 right-1/4 w-[400px] h-[400px] bg-blue-600/8 blur-[100px] rounded-full" />
        </div>

        <div className="relative max-w-4xl mx-auto px-4 text-center">
          {/* Badge */}
          <div className="inline-flex items-center gap-2 bg-red-600/10 border border-red-600/20 rounded-full px-4 py-1.5 mb-8">
            <span className="w-2 h-2 bg-red-500 rounded-full animate-pulse" />
            <span className="text-sm text-red-400 font-medium">Now streaming</span>
          </div>

          <h1 className="text-5xl sm:text-6xl lg:text-7xl font-bold text-white leading-tight mb-6">
            Unlimited movies,
            <br />
            <span className="text-transparent bg-clip-text bg-gradient-to-r from-red-500 to-red-600">
              TV shows &amp; more
            </span>
          </h1>

          <p className="text-lg sm:text-xl text-gray-400 max-w-2xl mx-auto mb-10 leading-relaxed">
            Watch anywhere. Cancel anytime. Ready to watch? Enter your email to create or restart your membership.
          </p>

          {/* CTA Row */}
          <div className="flex flex-col sm:flex-row items-center justify-center gap-4 mb-12">
            <div className="flex items-center bg-dark-800 border border-dark-700 rounded-lg overflow-hidden w-full max-w-md">
              <input
                type="email"
                placeholder="Email address"
                className="flex-1 bg-transparent text-white placeholder-gray-500 px-4 py-3.5 text-sm focus:outline-none"
              />
              <Link
                href="/signup"
                className="bg-red-600 hover:bg-red-700 text-white px-6 py-3.5 text-sm font-semibold transition-colors whitespace-nowrap flex items-center gap-2"
              >
                Get Started
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
                </svg>
              </Link>
            </div>
          </div>

          {/* Feature Cards */}
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-6 max-w-2xl mx-auto mt-16">
            {[
              {
                icon: (
                  <svg className="w-8 h-8" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 3v11.25A2.25 2.25 0 006 16.5h2.25m5.25-9v11.25A2.25 2.25 0 0013.5 16.5h2.25M6 16.5V21m6.75-4.5V21m3-9h3.75m-3.75 0V5.25A2.25 2.25 0 0115.75 3h-1.5m1.5 0H6" />
                  </svg>
                ),
                title: 'Stream Anywhere',
                desc: 'Watch on your phone, tablet, laptop, and TV.',
              },
              {
                icon: (
                  <svg className="w-8 h-8" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 13.5l10.5-11.25L12 10.5h8.25L9.75 21.75 12 13.5H3.75z" />
                  </svg>
                ),
                title: '4K + HDR',
                desc: 'Ultra HD quality on supported devices.',
              },
              {
                icon: (
                  <svg className="w-8 h-8" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75L11.25 15 15 9.75M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                  </svg>
                ),
                title: 'No Commitment',
                desc: 'Cancel anytime. No contracts, no hidden fees.',
              },
            ].map((feature) => (
              <div key={feature.title} className="text-center p-6 bg-dark-900/50 rounded-xl border border-dark-800/50">
                <div className="text-red-500 mb-3 flex justify-center">{feature.icon}</div>
                <h3 className="text-sm font-semibold text-white mb-1">{feature.title}</h3>
                <p className="text-xs text-gray-500">{feature.desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Divider Sections */}
      {[
        {
          title: 'Enjoy on your TV',
          desc: 'Watch on Smart TVs, Playstation, Xbox, Chromecast, Apple TV, Blu-ray players, and more.',
          flip: false,
        },
        {
          title: 'Download your shows',
          desc: 'Save your favorites and always have something to watch. Download on the go.',
          flip: true,
        },
        {
          title: 'Watch everywhere',
          desc: 'Stream unlimited movies and TV shows on your phone, tablet, laptop, and TV.',
          flip: false,
        },
      ].map((section, i) => (
        <section key={i} className="py-16 px-4 border-t border-dark-800">
          <div className={`max-w-6xl mx-auto flex flex-col ${section.flip ? 'md:flex-row-reverse' : 'md:flex-row'} items-center gap-12`}>
            <div className={`flex-1 ${section.flip ? 'text-right md:text-right' : 'text-left'}`}>
              <h2 className="text-3xl sm:text-4xl font-bold text-white mb-4">{section.title}</h2>
              <p className="text-lg text-gray-400 leading-relaxed">{section.desc}</p>
            </div>
            <div className="flex-1 flex justify-center">
              <div className="w-72 h-48 bg-dark-800 rounded-xl border border-dark-700 shimmer flex items-center justify-center">
                <svg className="w-12 h-12 text-gray-700" fill="none" viewBox="0 0 24 24" strokeWidth={1} stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M5.25 5.653c0-.856.917-1.398 1.667-.986l11.54 6.347a1.125 1.125 0 010 1.972l-11.54 6.347c-.75.412-1.667-.13-1.667-.986V5.653z" />
                </svg>
              </div>
            </div>
          </div>
        </section>
      ))}

      {/* FAQ */}
      <section className="py-16 px-4 border-t border-dark-800">
        <div className="max-w-2xl mx-auto">
          <h2 className="text-3xl font-bold text-white text-center mb-10">Frequently Asked Questions</h2>
          <div className="space-y-3">
            {[
              { q: 'What is Wildframe?', a: 'Wildframe is a streaming service that offers a wide variety of award-winning TV shows, movies, anime, documentaries, and more on thousands of internet-connected devices.' },
              { q: 'How much does Wildframe cost?', a: 'Watch Wildframe on your smartphone, tablet, Smart TV, laptop, or streaming device, all for one fixed monthly fee. Plans range from Free to $22.99 per month.' },
              { q: 'Where can I watch?', a: 'Watch anywhere, anytime. Sign in with your Wildframe account to watch instantly on the web at wildframe.com, or on any internet-connected device.' },
              { q: 'How do I cancel?', a: 'Wildframe is flexible. There are no pesky contracts and no commitments. You can easily cancel your account online in two clicks.' },
            ].map((faq) => (
              <details key={faq.q} className="bg-dark-900 rounded-lg border border-dark-800 overflow-hidden group">
                <summary className="px-5 py-5 text-base font-medium text-white cursor-pointer list-none flex items-center justify-between hover:bg-dark-800 transition-colors">
                  {faq.q}
                  <svg className="w-5 h-5 text-gray-400 transition-transform group-open:rotate-180 flex-shrink-0" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
                  </svg>
                </summary>
                <div className="px-5 pb-5 text-sm text-gray-400 border-t border-dark-800 pt-4 leading-relaxed">
                  {faq.a}
                </div>
              </details>
            ))}
          </div>
        </div>
      </section>

      {/* CTA */}
      <section className="py-16 px-4 border-t border-dark-800">
        <div className="max-w-xl mx-auto text-center">
          <p className="text-lg text-gray-400 mb-6">Ready to watch? Create your membership today.</p>
          <Link
            href="/signup"
            className="inline-flex items-center gap-2 bg-red-600 hover:bg-red-700 text-white px-8 py-3.5 rounded-lg font-semibold transition-colors text-base"
          >
            Get Started
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
            </svg>
          </Link>
        </div>
      </section>

      {/* Footer */}
      <footer className="py-10 px-4 border-t border-dark-800">
        <div className="max-w-6xl mx-auto text-center">
          <p className="text-xs text-gray-600">&copy; {new Date().getFullYear()} Wildframe. All rights reserved.</p>
        </div>
      </footer>
    </div>
  );
}
