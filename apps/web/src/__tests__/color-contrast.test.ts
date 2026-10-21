/**
 * Two colour contracts for the dark UI, both enforced here.
 *
 * 1. THE BRAND RED HAS TWO JOBS, AND THEY NEED TWO DIFFERENT VALUES.
 *
 * #e50914 is a FILL colour -- button backgrounds, borders, the focus ring.
 * As TEXT it fails WCAG 2.1 AA: 4.11:1 on the page background (#0b0b0b) and
 * 3.63:1 on a raised card (#1a1a1a), both under the 4.5:1 AA minimum for
 * normal text. The wordmarks, the section eyebrows and the red links were all
 * failing because of it.
 *
 *   brand colour as TEXT  -> `text-brand-400` (or `hover:text-brand-300`)
 *   brand colour as a FILL -> `bg-brand` / `bg-[#E50914]` / `border-brand`
 *
 * 2. THE MUTED TEXT RAMP WAS TOO DARK FOR A DARK UI.
 *
 * The stock `gray-500` (#737373) and `zinc-500` (#71717a) tokens are 3.7-4.2:1
 * against this app's surfaces and `zinc-600` (#52525b) is 2.3:1. A
 * real-browser audit of all 15 routes found 140 failing text nodes and every
 * one of them was one of those three tokens.
 *
 *   secondary text -> `text-muted`        (5.5:1 on the lightest surface)
 *   faintest label -> `text-muted-subtle` (4.8:1)
 *
 * The stock scales stay as they are: `bg-zinc-600` is the secondary button on
 * the admin confirm dialog and needs to stay dark for its white label.
 *
 * Part (a) enforces the rules across the source tree; part (b) re-checks the
 * palette values themselves, so neither rule can be satisfied by a palette
 * that fails.
 */
import { readFileSync, readdirSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const WEB_ROOT = path.resolve(HERE, '..', '..');
const REPO_ROOT = path.resolve(WEB_ROOT, '..', '..');
const SRC_ROOT = path.join(WEB_ROOT, 'src');
const TAILWIND_CONFIG = path.join(WEB_ROOT, 'tailwind.config.ts');
const GLOBALS_CSS = path.join(SRC_ROOT, 'app', 'globals.css');

/** WCAG 2.1 AA minimum contrast for normal-size text. */
const AA_TEXT_MIN = 4.5;
/** The page background set on `body` in src/app/globals.css. */
const PAGE_BACKGROUND = '#0b0b0b';
/** The raised card surface -- the palette's `dark.900`, used by Card/Modal. */
const RAISED_CARD = '#1a1a1a';

/** The raised surface of a form field (palette dark.800) -- lighter than
 *  any card, so muted text has to clear AA on it too. */
const RAISED_INPUT = '#2f2f2f';

const SOURCE_EXTENSIONS = new Set(['.ts', '.tsx', '.css']);

/**
 * Text-colour utilities that resolve to the brand red. `bg-brand`,
 * `bg-[#E50914]`, `border-[#E50914]`, `from-[#E50914]` and `shadow-[#E50914]`
 * are fills and stay legal -- only the TEXT colour utilities are banned.
 *
 * The palette tokens are included alongside the hex forms because they are the
 * same colour: `brand` (DEFAULT), `brand.500`, `red.500` and `netflix.500` are
 * all #e50914. `brand.300`/`brand.400` are different values and stay legal.
 *
 * The match is anchored to a whole class token, so a variant prefix
 * (`hover:text-red-500`) still matches while a fill, a different token, or a
 * URL that merely contains the string does not. A `/opacity` suffix is
 * allowed -- `text-red-500/80` is still a banned text colour.
 */
const BANNED_TEXT_CLASSES = [
  /^\.?(?:[!]?[^\s'"`:]*:)*[!]?text-\[(?:color:)?#e50914\](?![A-Za-z0-9_-])/i,
  /^\.?(?:[!]?[^\s'"`:]*:)*[!]?text-(?:brand|brand-500|netflix-500|red-500)(?![A-Za-z0-9_-])/,
  // The stock muted-text tokens. `gray-500` and `zinc-500` are 3.7-4.2:1 on
  // this app's dark surfaces, `zinc-600` is 2.3:1 and `gray-600` is 2.4:1 --
  // all four fail AA for normal text, and a real-browser audit of all 15
  // routes found 140 failing text nodes, every one of them one of these.
  // `gray-700` and `zinc-700` are darker still and would fail the same way;
  // they have no current use, but a token nobody is allowed to use cannot
  // come back. Use `text-muted` and `text-muted-subtle` instead. Only the
  // TEXT utilities are banned -- `bg-zinc-600` is the secondary button on the
  // admin confirm dialog and must stay dark for its white label.
  /^\.?(?:[!]?[^\s'"`:]*:)*[!]?text-(?:gray-500|gray-600|gray-700|zinc-500|zinc-600|zinc-700)(?![A-Za-z0-9_-])/,
];

/** A class is a whitespace- and quote-delimited token in the source. */
const CLASS_TOKEN = /[^\s'"`]+/g;

interface Violation {
  file: string;
  line: number;
  offender: string;
}

function sourceFiles(dir: string): string[] {
  const found: string[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      // Test files are not shipped to the browser and routinely quote the
      // banned spellings (including this file's own table above), so they are
      // outside the contract.
      if (entry.name === '__tests__') continue;
      found.push(...sourceFiles(full));
    } else if (SOURCE_EXTENSIONS.has(path.extname(entry.name))) {
      found.push(full);
    }
  }
  return found.sort();
}

/**
 * Blank out comments while preserving every newline, so a match still reports
 * the line it was written on. A comment that documents the rule must not read
 * as a violation of it.
 */
function stripComments(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, (block) => block.replace(/[^\n]/g, ' '))
    .replace(/(^|\s)\/\/[^\n]*/gm, (line, lead: string) =>
      lead + ' '.repeat(line.length - lead.length),
    );
}

function lineOf(source: string, index: number): number {
  return source.slice(0, index).split('\n').length;
}

/**
 * A CSS `color:` declaration is the brand colour as text even though it is
 * not a Tailwind utility. `border-color:`/`background-color:` are excluded --
 * the property name has to start the declaration.
 */
function cssColorViolations(file: string, source: string): Violation[] {
  const violations: Violation[] = [];
  const declaration = /(^|[;{])\s*color\s*:\s*([^;}]*)/g;
  for (const match of source.matchAll(declaration)) {
    const value = match[2];
    if (!/#e50914/i.test(value)) continue;
    violations.push({
      file: path.relative(REPO_ROOT, file),
      line: lineOf(source, match.index + match[0].indexOf('color')),
      offender: `color: ${value.trim()}`,
    });
  }
  return violations;
}

function findViolations(): Violation[] {
  const violations: Violation[] = [];
  for (const file of sourceFiles(SRC_ROOT)) {
    const source = stripComments(readFileSync(file, 'utf8'));
    for (const match of source.matchAll(CLASS_TOKEN)) {
      if (!BANNED_TEXT_CLASSES.some((pattern) => pattern.test(match[0]))) continue;
      violations.push({
        file: path.relative(REPO_ROOT, file),
        line: lineOf(source, match.index),
        offender: match[0],
      });
    }
    if (file.endsWith('.css')) {
      violations.push(...cssColorViolations(file, source));
    }
  }
  return violations;
}

/** The banned class tokens present in a piece of text, for the scanner's own tests. */
function bannedTokens(text: string): string[] {
  const found: string[] = [];
  for (const match of text.matchAll(CLASS_TOKEN)) {
    if (BANNED_TEXT_CLASSES.some((pattern) => pattern.test(match[0]))) {
      found.push(match[0]);
    }
  }
  return found;
}

/**
 * Parse one colour scale out of the Tailwind config as text.
 *
 * The config cannot be imported: it calls `require('@tailwindcss/typography')`
 * at module scope, which throws in the ESM test environment, and importing it
 * would also drag a file outside `tsconfig.json`'s `include` into the program
 * with a `require` that has no type.
 */
const scales = new Map<string, Record<string, string>>();

function tailwindColorScale(name: string): Record<string, string> {
  const cached = scales.get(name);
  if (cached) return cached;

  const config = readFileSync(TAILWIND_CONFIG, 'utf8');
  const block = new RegExp(`\\b${name}\\s*:\\s*\\{([^}]*)\\}`).exec(config);
  if (!block) {
    throw new Error(`no "${name}" colour block found in ${TAILWIND_CONFIG}`);
  }
  const scale: Record<string, string> = {};
  for (const match of block[1].matchAll(/([A-Za-z0-9]+)\s*:\s*["'](#[0-9a-fA-F]{3,8})["']/g)) {
    scale[match[1]] = match[2];
  }
  scales.set(name, scale);
  return scale;
}

function linearize(channel: number): number {
  const c = channel / 255;
  return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
}

function relativeLuminance(hex: string): number {
  const raw = hex.replace('#', '').trim();
  const full =
    raw.length === 3
      ? raw
          .split('')
          .map((c) => c + c)
          .join('')
      : raw;
  if (!/^[0-9a-fA-F]{6}$/.test(full)) {
    throw new Error(`unsupported hex colour: ${hex}`);
  }
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(full.slice(i, i + 2), 16));
  return 0.2126 * linearize(r) + 0.7152 * linearize(g) + 0.0722 * linearize(b);
}

function contrastRatio(a: string, b: string): number {
  const la = relativeLuminance(a);
  const lb = relativeLuminance(b);
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

describe('colour accessibility contract', () => {
  describe('rule enforcement', () => {
    it('never uses the brand red as a text colour', () => {
      const violations = findViolations();

      const detail = violations
        .map((v) => `  ${v.file}:${v.line} — ${v.offender}`)
        .join('\n');

      expect(
        violations,
        [
          'The brand red is a fill colour. As text it is 4.11:1 on the page',
          `background and 3.63:1 on a raised card, so it fails WCAG 2.1 AA for`,
          'normal text (4.5:1). Use `text-brand-400` for brand-coloured text, and',
          '`hover:text-brand-300` for hover states. `bg-brand`, `bg-[#E50914]`,',
          '`border-brand` and `shadow-[#E50914]` are fills and stay legal.',
          detail,
        ].join('\n'),
      ).toEqual([]);
    });

    it('accepts the legal fills and hover states', () => {
      // Guards the scanner itself: a rule that flags `bg-[#E50914]` or
      // `hover:text-brand-400` blocks the team and gets the rule deleted.
      const legal = [
        'bg-[#E50914]',
        'bg-[#E50914]/10',
        'border-[#E50914]',
        'from-[#e50914]',
        'shadow-[#E50914]',
        'bg-brand',
        'border-brand',
        'text-white',
        'text-brand-400',
        'text-brand-300',
        'hover:text-brand-400',
        'hover:text-brand-300',
        'focus-visible:ring-[#e50914]',
      ];

      expect(
        bannedTokens(legal.join(' ')),
        `the scanner flagged a legal form: ${legal.join(', ')}`,
      ).toEqual([]);
    });

    it('still catches the banned text colours', () => {
      // A scanner that matches nothing is not a contract.
      expect(bannedTokens('text-[#E50914]')).toEqual(['text-[#E50914]']);
      expect(bannedTokens('hover:text-red-500')).toEqual(['hover:text-red-500']);
      expect(bannedTokens('text-red-500/80')).toEqual(['text-red-500/80']);
      expect(bannedTokens('text-brand')).toEqual(['text-brand']);
      expect(bannedTokens('text-brand-500')).toEqual(['text-brand-500']);
      expect(bannedTokens('text-netflix-500')).toEqual(['text-netflix-500']);
      expect(bannedTokens('text-gray-500')).toEqual(['text-gray-500']);
      expect(bannedTokens('text-gray-600')).toEqual(['text-gray-600']);
      expect(bannedTokens('text-gray-700')).toEqual(['text-gray-700']);
      expect(bannedTokens('hover:text-zinc-500')).toEqual(['hover:text-zinc-500']);
      expect(bannedTokens('text-zinc-600')).toEqual(['text-zinc-600']);
      expect(bannedTokens('text-zinc-700')).toEqual(['text-zinc-700']);
    });

    it('does not read a URL or a comment as a violation', () => {
      expect(bannedTokens("'https://example.test/text-red-500'")).toEqual([]);
      expect(bannedTokens(stripComments('// use text-red-500 for the brand'))).toEqual([]);
    });

    it('accepts the accessible muted tokens and the dark fills that stay', () => {
      const legal = [
        'text-muted',
        'text-muted-subtle',
        'hover:text-muted',
        'bg-zinc-600',
        'ring-zinc-500/25',
        'bg-zinc-500/15',
        'border-gray-500',
        'text-gray-400',
        'text-zinc-400',
        'text-gray-300',
      ];

      expect(
        bannedTokens(legal.join(' ')),
        `the scanner flagged a legal form: ${legal.join(', ')}`,
      ).toEqual([]);
    });
  });

  describe('value enforcement', () => {
    it('keeps brand.400 legible as text on the page background', () => {
      const brand400 = tailwindColorScale('brand')['400'];
      expect(brand400, 'brand.400 is missing from tailwind.config.ts').toBeTruthy();

      const ratio = contrastRatio(brand400, PAGE_BACKGROUND);
      expect(
        ratio,
        `brand.400 (${brand400}) on the page background ${PAGE_BACKGROUND} is ${ratio.toFixed(2)}:1, under the ${AA_TEXT_MIN}:1 WCAG 2.1 AA minimum for normal text`,
      ).toBeGreaterThanOrEqual(AA_TEXT_MIN);
    });

    it('keeps brand.400 legible as text on a raised card', () => {
      const brand400 = tailwindColorScale('brand')['400'];
      expect(brand400, 'brand.400 is missing from tailwind.config.ts').toBeTruthy();

      const ratio = contrastRatio(brand400, RAISED_CARD);
      expect(
        ratio,
        `brand.400 (${brand400}) on the raised card ${RAISED_CARD} is ${ratio.toFixed(2)}:1, under the ${AA_TEXT_MIN}:1 WCAG 2.1 AA minimum for normal text`,
      ).toBeGreaterThanOrEqual(AA_TEXT_MIN);
    });

    it('keeps brand.500 strong enough to carry white button text', () => {
      const brand500 = tailwindColorScale('brand')['500'];
      expect(brand500, 'brand.500 is missing from tailwind.config.ts').toBeTruthy();

      const ratio = contrastRatio(brand500, '#ffffff');
      expect(
        ratio,
        `brand.500 (${brand500}) under white text is ${ratio.toFixed(2)}:1, under the ${AA_TEXT_MIN}:1 WCAG 2.1 AA minimum for normal text`,
      ).toBeGreaterThanOrEqual(AA_TEXT_MIN);
    });

    it('keeps the text colour and the fill colour as different values', () => {
      const brand = tailwindColorScale('brand');
      const brand400 = brand['400'];
      const brand500 = brand['500'];

      // Collapsing the scale back to a single value is the regression: the
      // text colour would silently become the failing fill colour again.
      expect(
        brand400.toLowerCase(),
        `brand.400 and brand.500 are both ${brand400} — the brand colour as text and as a fill must be different values`,
      ).not.toBe(brand500.toLowerCase());
    });

    it('measures against the surfaces the app actually uses', () => {
      // The contrast constants above are only meaningful while they match the
      // shipped palette and page background.
      const globals = readFileSync(GLOBALS_CSS, 'utf8').toLowerCase();
      expect(
        globals,
        `the page background is no longer ${PAGE_BACKGROUND} in src/app/globals.css — update PAGE_BACKGROUND in this test and re-check the ratio`,
      ).toContain(PAGE_BACKGROUND);

      expect(
        tailwindColorScale('dark')['900']?.toLowerCase(),
        `the raised card surface is no longer ${RAISED_CARD} (palette dark.900) — update RAISED_CARD in this test and re-check the ratio`,
      ).toBe(RAISED_CARD);

      expect(
        tailwindColorScale('dark')['800']?.toLowerCase(),
        `the form field surface is no longer ${RAISED_INPUT} (palette dark.800) — update RAISED_INPUT in this test and re-check the ratio`,
      ).toBe(RAISED_INPUT);
    });
  });

  describe('muted text palette', () => {
    it('keeps muted legible as text on the page background', () => {
      const muted = tailwindColorScale('muted')['DEFAULT'];
      expect(muted, 'muted.DEFAULT is missing from tailwind.config.ts').toBeTruthy();

      const ratio = contrastRatio(muted, PAGE_BACKGROUND);
      expect(
        ratio,
        `muted (${muted}) on the page background ${PAGE_BACKGROUND} is ${ratio.toFixed(2)}:1, under the ${AA_TEXT_MIN}:1 WCAG 2.1 AA minimum for normal text`,
      ).toBeGreaterThanOrEqual(AA_TEXT_MIN);
    });

    it('keeps muted legible as text on a raised card', () => {
      const muted = tailwindColorScale('muted')['DEFAULT'];
      expect(muted, 'muted.DEFAULT is missing from tailwind.config.ts').toBeTruthy();

      const ratio = contrastRatio(muted, RAISED_CARD);
      expect(
        ratio,
        `muted (${muted}) on the raised card ${RAISED_CARD} is ${ratio.toFixed(2)}:1, under the ${AA_TEXT_MIN}:1 WCAG 2.1 AA minimum for normal text`,
      ).toBeGreaterThanOrEqual(AA_TEXT_MIN);
    });

    it('keeps muted legible as a label on a form field', () => {
      // The account form labels sit on the raised input surface, which is
      // lighter than any card. A muted value that only clears AA on the page
      // background still fails there -- it did, at 4.22:1.
      const muted = tailwindColorScale('muted')['DEFAULT'];
      expect(muted, 'muted.DEFAULT is missing from tailwind.config.ts').toBeTruthy();

      const ratio = contrastRatio(muted, RAISED_INPUT);
      expect(
        ratio,
        `muted (${muted}) on the form field surface ${RAISED_INPUT} is ${ratio.toFixed(2)}:1, under the ${AA_TEXT_MIN}:1 WCAG 2.1 AA minimum for normal text`,
      ).toBeGreaterThanOrEqual(AA_TEXT_MIN);
    });

    it('keeps the faintest label legible as text on a raised card', () => {
      const subtle = tailwindColorScale('muted')['subtle'];
      expect(subtle, 'muted.subtle is missing from tailwind.config.ts').toBeTruthy();

      const ratio = contrastRatio(subtle, RAISED_CARD);
      expect(
        ratio,
        `muted.subtle (${subtle}) on the raised card ${RAISED_CARD} is ${ratio.toFixed(2)}:1, under the ${AA_TEXT_MIN}:1 WCAG 2.1 AA minimum for normal text`,
      ).toBeGreaterThanOrEqual(AA_TEXT_MIN);
    });

    it('keeps the two muted levels as different values', () => {
      const muted = tailwindColorScale('muted');
      const defaultValue = muted['DEFAULT'];
      const subtle = muted['subtle'];

      // Collapsing them would erase the type hierarchy the ramp exists for.
      expect(
        defaultValue.toLowerCase(),
        `muted.DEFAULT and muted.subtle are both ${defaultValue} — the ramp needs two distinct levels`,
      ).not.toBe(subtle.toLowerCase());
    });

    it('keeps the faintest level dimmer than the secondary level', () => {
      const muted = tailwindColorScale('muted');
      const defaultValue = muted['DEFAULT'];
      const subtle = muted['subtle'];

      expect(
        contrastRatio(subtle, RAISED_CARD),
        `muted.subtle (${subtle}) must stay dimmer than muted (${defaultValue}) on the raised card`,
      ).toBeLessThan(contrastRatio(defaultValue, RAISED_CARD));
    });
  });
});
