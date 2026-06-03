'use client';

import Link from 'next/link';

export default function HomePage() {
  return (
    <div className="min-h-screen bg-white">
      {/* Navigation */}
      <nav className="fixed w-full top-0 bg-white/95 backdrop-blur border-b border-gray-200 z-50">
        <div className="max-w-7xl mx-auto px-4 py-4 flex items-center justify-between">
          <div className="text-2xl font-bold text-gray-900">YourApp</div>
          <div className="hidden md:flex gap-8">
            <a href="#features" className="text-gray-600 hover:text-gray-900">Features</a>
            <a href="#pricing" className="text-gray-600 hover:text-gray-900">Pricing</a>
            <a href="#docs" className="text-gray-600 hover:text-gray-900">Docs</a>
          </div>
          <div className="flex gap-4">
            <Link href="/login" className="text-gray-600 hover:text-gray-900 font-medium">Sign in</Link>
            <Link href="/signup" className="bg-gray-900 text-white px-6 py-2 rounded-lg hover:bg-gray-800 transition">Get started</Link>
          </div>
        </nav>
      </nav>

      {/* Hero Section */}
      <section className="pt-32 pb-20 px-4">
        <div className="max-w-4xl mx-auto text-center">
          <h1 className="text-6xl font-bold text-gray-900 mb-6 leading-tight">
            Build faster, deploy smarter
          </h1>
          <p className="text-xl text-gray-600 mb-8 max-w-2xl mx-auto">
            Everything you need to build, test, and deploy modern applications. Lightning-fast, secure, and infinitely scalable.
          </p>
          <div className="flex gap-4 justify-center">
            <Link href="/signup" className="bg-gray-900 text-white px-8 py-4 rounded-lg hover:bg-gray-800 transition text-lg font-medium">
              Start for free
            </Link>
            <button className="border-2 border-gray-900 text-gray-900 px-8 py-4 rounded-lg hover:bg-gray-50 transition text-lg font-medium">
              Watch demo
            </button>
          </div>
          <div className="mt-12 p-8 bg-gradient-to-b from-gray-50 to-transparent rounded-2xl border border-gray-200">
            <div className="bg-gray-900 text-white rounded-lg p-6 font-mono text-sm">
              <div className="text-green-400">$ npm create app@latest</div>
              <div className="text-gray-400 mt-2">✔ Project created successfully</div>
            </div>
          </div>
        </div>
      </section>

      {/* Features Section */}
      <section id="features" className="bg-gray-50 py-20 px-4">
        <div className="max-w-7xl mx-auto">
          <h2 className="text-4xl font-bold text-gray-900 text-center mb-16">Everything you need</h2>
          <div className="grid md:grid-cols-3 gap-8">
            {[
              {
                icon: '⚡',
                title: 'Lightning Fast',
                desc: 'Global CDN with automatic optimization. Deploy in milliseconds.'
              },
              {
                icon: '🔒',
                title: 'Enterprise Security',
                desc: 'DDoS protection, SSL/TLS encryption, and compliance certifications.'
              },
              {
                icon: '♾️',
                title: 'Infinitely Scalable',
                desc: 'Auto-scaling infrastructure that grows with your traffic.'
              },
              {
                icon: '📊',
                title: 'Analytics & Monitoring',
                desc: 'Real-time insights into performance and user behavior.'
              },
              {
                icon: '🔄',
                title: 'Continuous Integration',
                desc: 'Automatic builds and deployments on every git push.'
              },
              {
                icon: '🌍',
                title: 'Global Edge Network',
                desc: 'Serve content from 200+ data centers worldwide.'
              },
            ].map((feature, i) => (
              <div key={i} className="bg-white p-8 rounded-xl border border-gray-200 hover:border-gray-300 hover:shadow-lg transition">
                <div className="text-4xl mb-4">{feature.icon}</div>
                <h3 className="text-xl font-bold text-gray-900 mb-3">{feature.title}</h3>
                <p className="text-gray-600">{feature.desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Pricing Section */}
      <section id="pricing" className="py-20 px-4">
        <div className="max-w-7xl mx-auto">
          <h2 className="text-4xl font-bold text-gray-900 text-center mb-16">Simple, transparent pricing</h2>
          <div className="grid md:grid-cols-3 gap-8">
            {[
              {
                name: 'Starter',
                price: 'Free',
                features: ['Up to 1 project', 'Basic analytics', 'Community support'],
              },
              {
                name: 'Pro',
                price: '$19',
                period: '/month',
                features: ['Unlimited projects', 'Advanced analytics', 'Priority support', 'Custom domains'],
                highlight: true,
              },
              {
                name: 'Enterprise',
                price: 'Custom',
                features: ['Dedicated infrastructure', 'SLA guarantee', 'Custom integrations', 'White-label'],
              },
            ].map((plan, i) => (
              <div key={i} className={`p-8 rounded-xl border-2 transition ${
                plan.highlight 
                  ? 'border-gray-900 bg-gray-900 text-white' 
                  : 'border-gray-200 bg-white hover:border-gray-300'
              }`}>
                <h3 className="text-2xl font-bold mb-2">{plan.name}</h3>
                <div className="text-3xl font-bold mb-6">
                  {plan.price}
                  {plan.period && <span className="text-base font-normal">{plan.period}</span>}
                </div>
                <button className={`w-full py-3 rounded-lg font-medium mb-8 transition ${
                  plan.highlight
                    ? 'bg-white text-gray-900 hover:bg-gray-100'
                    : 'border border-gray-300 text-gray-900 hover:bg-gray-50'
                }`}>
                  Get started
                </button>
                <ul className="space-y-4">
                  {plan.features.map((feature, j) => (
                    <li key={j} className="flex items-center gap-3">
                      <span className="text-lg">✓</span>
                      <span>{feature}</span>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Testimonials */}
      <section className="bg-gray-50 py-20 px-4">
        <div className="max-w-7xl mx-auto">
          <h2 className="text-4xl font-bold text-gray-900 text-center mb-16">Loved by developers</h2>
          <div className="grid md:grid-cols-3 gap-8">
            {[
              {
                quote: 'Deployment just became effortless. Amazing platform.',
                author: 'Sarah Chen',
                role: 'Founder, TechCo',
              },
              {
                quote: 'The performance improvements are incredible. Highly recommend.',
                author: 'James Wilson',
                role: 'CTO, StartupXYZ',
              },
              {
                quote: 'Customer support is outstanding. They actually care.',
                author: 'Maria Garcia',
                role: 'Lead Developer, Agency',
              },
            ].map((testimonial, i) => (
              <div key={i} className="bg-white p-8 rounded-xl border border-gray-200">
                <div className="text-yellow-400 mb-4">★★★★★</div>
                <p className="text-gray-600 mb-6 italic">"{testimonial.quote}"</p>
                <div>
                  <p className="font-bold text-gray-900">{testimonial.author}</p>
                  <p className="text-gray-600 text-sm">{testimonial.role}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* CTA Section */}
      <section className="bg-gray-900 text-white py-20 px-4">
        <div className="max-w-4xl mx-auto text-center">
          <h2 className="text-4xl font-bold mb-6">Ready to get started?</h2>
          <p className="text-xl text-gray-300 mb-8">Join thousands of developers building the future.</p>
          <Link href="/signup" className="bg-white text-gray-900 px-8 py-4 rounded-lg hover:bg-gray-100 transition text-lg font-medium inline-block">
            Create free account
          </Link>
        </div>
      </section>

      {/* Footer */}
      <footer className="bg-white border-t border-gray-200 py-12 px-4">
        <div className="max-w-7xl mx-auto">
          <div className="grid md:grid-cols-4 gap-8 mb-8">
            <div>
              <h4 className="font-bold text-gray-900 mb-4">Product</h4>
              <ul className="space-y-2 text-gray-600">
                <li><a href="#" className="hover:text-gray-900">Features</a></li>
                <li><a href="#" className="hover:text-gray-900">Pricing</a></li>
                <li><a href="#" className="hover:text-gray-900">Security</a></li>
              </ul>
            </div>
            <div>
              <h4 className="font-bold text-gray-900 mb-4">Developers</h4>
              <ul className="space-y-2 text-gray-600">
                <li><a href="#" className="hover:text-gray-900">Documentation</a></li>
                <li><a href="#" className="hover:text-gray-900">API Reference</a></li>
                <li><a href="#" className="hover:text-gray-900">Support</a></li>
              </ul>
            </div>
            <div>
              <h4 className="font-bold text-gray-900 mb-4">Company</h4>
              <ul className="space-y-2 text-gray-600">
                <li><a href="#" className="hover:text-gray-900">About</a></li>
                <li><a href="#" className="hover:text-gray-900">Blog</a></li>
                <li><a href="#" className="hover:text-gray-900">Careers</a></li>
              </ul>
            </div>
            <div>
              <h4 className="font-bold text-gray-900 mb-4">Legal</h4>
              <ul className="space-y-2 text-gray-600">
                <li><a href="#" className="hover:text-gray-900">Privacy</a></li>
                <li><a href="#" className="hover:text-gray-900">Terms</a></li>
                <li><a href="#" className="hover:text-gray-900">Cookies</a></li>
              </ul>
            </div>
          </div>
          <div className="border-t border-gray-200 pt-8 flex justify-between items-center">
            <p className="text-gray-600">© 2024 YourApp. All rights reserved.</p>
            <div className="flex gap-6">
              <a href="#" className="text-gray-600 hover:text-gray-900">Twitter</a>
              <a href="#" className="text-gray-600 hover:text-gray-900">GitHub</a>
              <a href="#" className="text-gray-600 hover:text-gray-900">Discord</a>
            </div>
          </div>
        </div>
      </footer>
    </div>
  );
}
