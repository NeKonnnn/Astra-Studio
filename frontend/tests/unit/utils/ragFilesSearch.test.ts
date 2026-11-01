import {
  filterByRagFilenameQuery,
  normalizeRagFilenameQuery,
  ragFilenameMatchesQuery,
} from '@/utils/ragFilesSearch';

describe('ragFilesSearch', () => {
  it('normalizes the query', () => {
    expect(normalizeRagFilenameQuery('  Отчёт.PDF ')).toBe('отчёт.pdf');
  });

  it('matches by case-insensitive substring', () => {
    expect(ragFilenameMatchesQuery('Годовой_Отчёт.pdf', 'отчёт')).toBe(true);
    expect(ragFilenameMatchesQuery('a.txt', 'b')).toBe(false);
  });

  it('matches everything for an empty query', () => {
    expect(ragFilenameMatchesQuery('x', '')).toBe(true);
    expect(ragFilenameMatchesQuery('x', '   ')).toBe(true);
  });

  it('tolerates a missing filename', () => {
    expect(ragFilenameMatchesQuery(undefined as unknown as string, 'a')).toBe(false);
  });

  it('filters items and keeps their order', () => {
    const items = [{ filename: 'a.pdf' }, { filename: 'B.docx' }, { filename: 'ab.txt' }];

    expect(filterByRagFilenameQuery(items, 'AB')).toEqual([{ filename: 'ab.txt' }]);
    expect(filterByRagFilenameQuery(items, 'a')).toEqual([{ filename: 'a.pdf' }, { filename: 'ab.txt' }]);
  });

  it('returns a copy for an empty query', () => {
    const items = [{ filename: 'a' }];
    const result = filterByRagFilenameQuery(items, '');

    expect(result).toEqual(items);
    expect(result).not.toBe(items);
  });
});
