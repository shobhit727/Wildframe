'use client';

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
  className = '',
  dark = false,
}: SectionProps) {
  return (
    <section className={`py-20 px-4 \${dark ? 'bg-gray-900 text-white' : 'bg-white'} \${className}`}>
      <div className="max-w-7xl mx-auto">
        {title && (
          <div className="text-center mb-16">
            <h2 className="text-4xl font-bold mb-4">{title}</h2>
            {subtitle && <p className={dark ? 'text-gray-300' : 'text-gray-600'}>{subtitle}</p>}
          </div>
        )}
        {children}
      </div>
    </section>
  );
}
