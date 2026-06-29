'use client';

import { clsx } from 'clsx';

interface SectionProps {
  children: React.ReactNode;
  title?: string;
  subtitle?: string;
  className?: string;
  dark?: boolean;
}

export function Section({
  children,
  title,
  subtitle,
  className,
  dark = false,
}: SectionProps) {
  return (
    <section className={clsx('py-20 px-4', dark ? 'bg-dark-900 text-white' : 'bg-dark-950 text-white', className)}>
      <div className="max-w-7xl mx-auto">
        {title && (
          <div className="text-center mb-16">
            <h2 className="text-4xl font-bold mb-4">{title}</h2>
            {subtitle && <p className={dark ? 'text-gray-300' : 'text-gray-400'}>{subtitle}</p>}
          </div>
        )}
        {children}
      </div>
    </section>
  );
}
