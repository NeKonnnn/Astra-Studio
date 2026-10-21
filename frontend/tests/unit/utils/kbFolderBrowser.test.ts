/**
 * @jest-environment jsdom
 */
import {
  basenameOf,
  filterKbDocsByQuery,
  getDocRelativePath,
  joinPath,
  listKbFolderEntries,
  normalizeRelativePath,
} from '@/utils/kbFolderBrowser';

describe('kbFolderBrowser', () => {
  it('normalizes and joins paths', () => {
    expect(normalizeRelativePath('\\a\\b\\')).toBe('a/b');
    expect(joinPath('docs', 'a.txt')).toBe('docs/a.txt');
    expect(basenameOf('docs/sub/x.md')).toBe('x.md');
  });

  it('lists folders and files for current path', () => {
    const docs = [
      { id: 1, filename: 'root.txt' },
      { id: 2, filename: 'a.txt', metadata: { relative_path: 'folder/a.txt' } },
      { id: 3, filename: 'b.txt', metadata: { relative_path: 'folder/nested/b.txt' } },
    ];
    expect(getDocRelativePath(docs[0])).toBe('root.txt');
    const root = listKbFolderEntries(docs, '');
    expect(root.folders).toEqual(['folder']);
    expect(root.files.map((d) => d.id)).toEqual([1]);
    const folder = listKbFolderEntries(docs, 'folder');
    expect(folder.folders).toEqual(['nested']);
    expect(folder.files.map((d) => d.id)).toEqual([2]);
  });

  it('filters by query', () => {
    const docs = [
      { id: 1, filename: 'Alpha.md' },
      { id: 2, filename: 'beta.txt', metadata: { relative_path: 'guides/beta.txt' } },
    ];
    expect(filterKbDocsByQuery(docs, 'beta').map((d) => d.id)).toEqual([2]);
  });
});
