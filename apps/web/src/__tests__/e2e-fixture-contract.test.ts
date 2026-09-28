/**
 * The E2E fixtures and the content normalizer must agree on `audience_score`.
 *
 * `audience_score` is a 0-100 field: `services/content-service/app/models/__init__.py`
 * declares it as "0-100 from user ratings", and `normalizeContent` treats it that
 * way -- `rating = audience_score / 10` for the star display, and
 * `matchPercentage = audience_score` for the "N% Match" label.
 *
 * The E2E fixtures carried 0-10 ratings (`audience_score: 8.4`), so every card
 * rendered "8% Match" and a "0.8" star while browse.spec.ts and watch.spec.ts
 * assert "84% Match" and "★ 8.4". Those two Playwright failures were a data
 * problem, not a UI problem, and they were expensive to diagnose because the
 * Playwright web server has to boot before the symptom is visible at all.
 *
 * This test closes that loop in milliseconds: it normalizes the real fixture, so
 * a scale drift in either place fails here first. `apps/web/tsconfig.json` did
 * not include the e2e directory before, which is how a required-field and a
 * wrong-scale value could both reach CI unnoticed.
 */
import { describe, expect, it } from 'vitest';

import { normalizeContent } from '@/api/client';
import type { BackendContent } from '@/types';
import { MOVIES } from '../../e2e/fixtures';

describe('E2E fixtures honour the 0-100 audience_score contract', () => {
  const nightfall = MOVIES.find((m) => m.title === 'Nightfall Protocol');

  it('has the Nightfall Protocol fixture the specs assert against', () => {
    expect(nightfall).toBeDefined();
  });

  it('renders the exact strings browse.spec.ts and watch.spec.ts assert', () => {
    // `FixtureContent` is a deliberately partial stand-in for the API response --
    // the Playwright mocks serve it in place of a real body -- so it does not
    // structurally satisfy every field of `BackendContent`. What matters here is
    // the fields normalization actually reads, and those the fixture does supply.
    const content = normalizeContent(nightfall as unknown as BackendContent);

    // watch.spec.ts:36 / browse.spec.ts:42 -> "84% Match"
    expect(`${Math.round(content.matchPercentage)}% Match`).toBe('84% Match');
    // watch.spec.ts:37 -> "★ 8.4"
    expect(`★ ${content.rating.toFixed(1)}`).toBe('★ 8.4');
  });

  it('keeps every fixture on the same scale, not just the asserted one', () => {
    for (const movie of MOVIES) {
      expect(movie.audience_score, `${movie.title} audience_score`).toBeGreaterThanOrEqual(0);
      expect(movie.audience_score, `${movie.title} audience_score`).toBeLessThanOrEqual(100);
      // A 0-10 rating written into a 0-100 field is the exact bug this guards.
      expect(movie.audience_score, `${movie.title} looks like a 0-10 rating`).toBeGreaterThan(10);
    }
  });
});
