/**
 * StatusBadge — small pill badge for user/alert/flag statuses.
 * Dark theme, color-mapped by status keyword.
 */
import clsx from 'clsx';

type Tone = 'green' | 'red' | 'amber' | 'sky' | 'zinc' | 'purple';

const TONES: Record<Tone, string> = {
  green: 'bg-green-500/15 text-green-400 ring-green-500/25',
  red: 'bg-red-500/15 text-red-400 ring-red-500/25',
  amber: 'bg-amber-500/15 text-amber-400 ring-amber-500/25',
  sky: 'bg-sky-500/15 text-sky-400 ring-sky-500/25',
  zinc: 'bg-zinc-500/15 text-zinc-300 ring-zinc-500/25',
  purple: 'bg-purple-500/15 text-purple-400 ring-purple-500/25',
};

const STATUS_TONE: Record<string, Tone> = {
  active: 'green',
  suspended: 'amber',
  banned: 'red',
  flagged: 'amber',
  removed: 'red',
  info: 'sky',
  warning: 'amber',
  critical: 'red',
  movie: 'purple',
  show: 'sky',
  episode: 'zinc',
};

export function statusTone(status: string): Tone {
  return STATUS_TONE[status] ?? 'zinc';
}

export function StatusBadge({
  status,
  tone,
  className,
}: {
  status: string;
  tone?: Tone;
  className?: string;
}) {
  const t = tone ?? statusTone(status);
  return (
    <span
      className={clsx(
        'inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset capitalize',
        TONES[t],
        className,
      )}
    >
      <span className={clsx('h-1.5 w-1.5 rounded-full', {
        'bg-green-400': t === 'green',
        'bg-red-400': t === 'red',
        'bg-amber-400': t === 'amber',
        'bg-sky-400': t === 'sky',
        'bg-zinc-300': t === 'zinc',
        'bg-purple-400': t === 'purple',
      })} />
      {status}
    </span>
  );
}
