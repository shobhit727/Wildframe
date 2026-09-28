import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { expect, test } from '@playwright/test';

import { ALL_CONTENT, toContentListItem } from './fixtures';

function findSchemaPath(): string {
  const candidates = [
    resolve(process.cwd(), 'services/content-service/app/schemas/__init__.py'),
    resolve(process.cwd(), '../../services/content-service/app/schemas/__init__.py'),
  ];
  const path = candidates.find((candidate) => existsSync(candidate));
  if (!path) throw new Error('Could not locate content-service schema source');
  return path;
}

function fieldsForClass(source: string, className: string): string[] {
  const match = source.match(
    new RegExp(`class ${className}\\(BaseModel\\):([\\s\\S]*?)(?=\\n\\nclass |$)`)
  );
  if (!match) throw new Error(`Could not find ${className} in content-service schema`);

  return [...match[1].matchAll(/^    ([A-Za-z_]\\w*):/gm)].map((m) => m[1]);
}

test('content E2E list mocks follow the backend response schema', () => {
  const schema = readFileSync(findSchemaPath(), 'utf8');
  const contentListFields = fieldsForClass(schema, 'ContentListResponse').sort();
  const genreFields = fieldsForClass(schema, 'GenreResponse').sort();

  for (const fixture of ALL_CONTENT) {
    const item = toContentListItem(fixture);

    expect(Object.keys(item).sort()).toEqual(contentListFields);
    expect(item).not.toHaveProperty('duration_minutes');
    expect(item).not.toHaveProperty('release_date');
    expect(item).not.toHaveProperty('matchPercentage');

    for (const genre of item.genres) {
      expect(Object.keys(genre).sort()).toEqual(genreFields);
    }
  }
});
