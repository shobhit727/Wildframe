'use client';

export function ContentSkeleton() {
  return (
    <div className="animate-pulse">
      <div className="h-64 w-full bg-gray-800 rounded-lg mb-2" />
      <div className="h-6 w-32 bg-gray-800 rounded mb-2" />
      <div className="h-4 w-24 bg-gray-800 rounded" />
    </div>
  );
}

export function ContentGridSkeleton({ count = 6 }: { count?: number }) {
  return (
    <div className="grid grid-cols-1 md:grid-cols-3 lg:grid-cols-4 gap-4">
      {Array.from({ length: count }).map((_, i) => (
        <ContentSkeleton key={i} />
      ))}
    </div>
  );
}
