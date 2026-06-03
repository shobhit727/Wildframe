'use client';

interface CardProps {
  children: React.ReactNode;
  className?: string;
  hover?: boolean;
}

export function Card({ children, className = '', hover = true }: CardProps) {
  return (
    <div
      className={`bg-white p-8 rounded-xl border border-gray-200 \${
        hover ? 'hover:shadow-lg hover:border-gray-300' : ''
      } transition \${className}`}
    >
      {children}
    </div>
  );
}
