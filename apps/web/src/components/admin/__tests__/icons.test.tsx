/**
 * The hand-rolled admin SVG icon set.
 *
 * There is no icon dependency, so these components *are* the design system for
 * the admin chrome. The shared `base` props (size, viewBox, stroke styling)
 * are what keeps 19 icons visually consistent; a component that forgets to
 * spread `base` would render at the browser default 300x150 and blow out its
 * toolbar, so that is what these assertions pin down.
 */
import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import * as Icons from '@/components/admin/icons';

const NAMES = Object.keys(Icons).sort() as Array<keyof typeof Icons>;

const EXPECTED = [
  'ActivityIcon',
  'BanIcon',
  'BellIcon',
  'CheckIcon',
  'ChevronDownIcon',
  'ChevronUpIcon',
  'ClipboardIcon',
  'DashboardIcon',
  'DollarIcon',
  'FlagIcon',
  'LogoutIcon',
  'MenuIcon',
  'MoreIcon',
  'PlayIcon',
  'PlusIcon',
  'RefreshIcon',
  'SearchIcon',
  'SettingsIcon',
  'TrashIcon',
  'UsersIcon',
  'XIcon',
] as const;

describe('admin icon set', () => {
  it('exports exactly the documented icon set', () => {
    expect(NAMES).toEqual([...EXPECTED].sort());
  });

  it.each(EXPECTED)('%s renders an 18x18 svg on the shared 24-unit viewBox', (name) => {
    const Icon = Icons[name];
    const { container } = render(<Icon />);
    const svg = container.querySelector('svg');

    expect(svg).not.toBeNull();
    expect(svg).toHaveAttribute('width', '18');
    expect(svg).toHaveAttribute('height', '18');
    expect(svg).toHaveAttribute('viewBox', '0 0 24 24');
  });

  it.each(EXPECTED)('%s is a stroked outline, not a filled silhouette', (name) => {
    const Icon = Icons[name];
    const { container } = render(<Icon />);
    const svg = container.querySelector('svg')!;

    // Mixed fill/stroke is what makes an icon set look accidental.
    expect(svg.getAttribute('fill')).toBe('none');
    expect(svg.getAttribute('stroke')).toBe('currentColor');
    expect(svg.getAttribute('stroke-width')).toBe('2');
    expect(svg.getAttribute('stroke-linecap')).toBe('round');
    expect(svg.getAttribute('stroke-linejoin')).toBe('round');
  });

  it.each(EXPECTED)('%s draws at least one shape', (name) => {
    const Icon = Icons[name];
    const { container } = render(<Icon />);
    const svg = container.querySelector('svg')!;

    // A shared `<svg>` wrapper with no children renders as an invisible gap.
    expect(svg.children.length).toBeGreaterThan(0);
  });

  it.each(EXPECTED)('%s inherits colour from its parent', (name) => {
    const Icon = Icons[name];
    const { container } = render(<Icon />);

    // `currentColor` (asserted above) only works if no hard-coded colour is set.
    expect(container.innerHTML).not.toMatch(/#[0-9a-fA-F]{3,6}/);
  });

  it('lets a caller override the size and add a class', () => {
    const { container } = render(<Icons.SearchIcon width={32} height={32} className="text-red-400" />);
    const svg = container.querySelector('svg')!;

    expect(svg).toHaveAttribute('width', '32');
    expect(svg).toHaveAttribute('height', '32');
    expect(svg.getAttribute('class')).toBe('text-red-400');
  });

  it('lets a caller restyle the stroke', () => {
    const { container } = render(<Icons.FlagIcon stroke="#e50914" />);
    expect(container.querySelector('svg')).toHaveAttribute('stroke', '#e50914');
  });

  it('exposes no accessible name of its own, leaving it to the caller', () => {
    const { container } = render(<Icons.BellIcon />);
    const svg = container.querySelector('svg')!;

    // Icons are decorative; a stray <title> would double-announce next to a
    // labelled button.
    expect(svg.querySelector('title')).toBeNull();
    expect(svg).not.toHaveAttribute('aria-label');
    expect(svg).not.toHaveAttribute('role');
  });

  it('renders distinct geometry per icon', () => {
    const shapes = EXPECTED.map((name) => {
      const Icon = Icons[name];
      const { container } = render(<Icon />);
      return container.innerHTML;
    });

    // Guards against two icons accidentally sharing a body (copy-paste).
    expect(new Set(shapes).size).toBe(EXPECTED.length);
  });
});
