'use client';

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <div className="min-h-screen bg-dark-950 flex items-center justify-center px-4">
      <div className="text-center">
        <h1 className="text-6xl font-bold text-white mb-4">Oops</h1>
        <p className="text-xl text-gray-400 mb-4">{error.message || 'Something went wrong'}</p>
        {error.digest && (
          <p className="text-xs text-gray-500 mb-8">Error ID: {error.digest}</p>
        )}
        <button
          onClick={reset}
          className="bg-red-600 hover:bg-red-700 text-white px-6 py-3 rounded-lg font-medium transition-colors"
        >
          Try again
        </button>
      </div>
    </div>
  );
}