'use client';

export function ContentSkeleton() {
  return (
    <div className="animate-pulse">
      <div className="aspect-[2/3] w-full bg-[#2f2f2f] mb-2 shimmer" />
      <div className="h-4 w-3/4 bg-[#2f2f2f] mb-1.5 shimmer" />
      <div className="h-3 w-1/2 bg-[#2f2f2f] shimmer" />
    </div>
  );
}

export function ContentGridSkeleton({ count = 6 }: { count?: number }) {
  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-3 px-4 sm:px-6 lg:px-8">
      {Array.from({ length: count }).map((_, i) => (
        <ContentSkeleton key={i} />
      ))}
    </div>
  );
}

export function RowSkeleton() {
  return (
    <section>
      <div className="h-6 w-40 bg-[#2f2f2f] mb-4 mx-4 sm:mx-6 lg:mx-8 shimmer" />
      <div className="flex gap-3 overflow-hidden px-4 sm:px-6 lg:px-8">
        {Array.from({ length: 7 }).map((_, i) => (
          <div key={i} className="flex-shrink-0 w-[160px]">
            <ContentSkeleton />
          </div>
        ))}
      </div>
    </section>
  );
}

export function HeroSkeleton() {
  return (
    <section className="relative w-full h-[70vh] min-h-[480px] bg-[#1a1a1a] shimmer">
      <div className="absolute bottom-[15%] left-8 max-w-xl space-y-4">
        <div className="h-4 w-20 bg-[#2f2f2f] shimmer" />
        <div className="h-10 w-80 bg-[#2f2f2f] shimmer" />
        <div className="h-4 w-60 bg-[#2f2f2f] shimmer" />
        <div className="h-16 w-96 bg-[#2f2f2f] shimmer" />
        <div className="flex gap-3">
          <div className="h-10 w-28 bg-[#2f2f2f] shimmer" />
          <div className="h-10 w-28 bg-[#2f2f2f] shimmer" />
        </div>
      </div>
    </section>
  );
}

export function ProfileSkeleton() {
  return (
    <div className="animate-pulse space-y-6">
      <div className="h-8 w-48 bg-[#2f2f2f] shimmer" />
      <div className="bg-[#1a1a1a] rounded-lg p-8 space-y-4">
        <div className="h-6 w-32 bg-[#2f2f2f] shimmer" />
        <div className="h-10 w-full bg-[#2f2f2f] shimmer" />
        <div className="h-10 w-full bg-[#2f2f2f] shimmer" />
      </div>
    </div>
  );
}
