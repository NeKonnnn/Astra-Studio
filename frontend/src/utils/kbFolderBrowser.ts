/** Навигация по каталогам внутри базы знаний (relative_path в metadata). */

export type KbDocPathRef = {
  id: number;
  filename: string;
  metadata?: {
    knowledge_base_id?: number;
    agent_id?: number;
    relative_path?: string;
  } | null;
};

export function normalizeRelativePath(raw: string): string {
  return String(raw || '')
    .replace(/\\/g, '/')
    .replace(/^\.\/+/, '')
    .replace(/^\/+/, '')
    .replace(/\/+/g, '/')
    .replace(/\/+$/, '');
}

/** Путь документа: metadata.relative_path или просто filename. */
export function getDocRelativePath(doc: KbDocPathRef): string {
  const fromMeta = doc.metadata?.relative_path;
  if (fromMeta && String(fromMeta).trim()) {
    return normalizeRelativePath(String(fromMeta));
  }
  return normalizeRelativePath(doc.filename);
}

export function basenameOf(path: string): string {
  const n = normalizeRelativePath(path);
  const i = n.lastIndexOf('/');
  return i < 0 ? n : n.slice(i + 1);
}

export function joinPath(parent: string, child: string): string {
  const p = normalizeRelativePath(parent);
  const c = normalizeRelativePath(child);
  if (!p) return c;
  if (!c) return p;
  return `${p}/${c}`;
}

export type KbFolderListing<T extends KbDocPathRef> = {
  folders: string[];
  files: T[];
};

/** Содержимое текущего каталога (папки + файлы на этом уровне). */
export function listKbFolderEntries<T extends KbDocPathRef>(
  docs: readonly T[],
  currentPath: string,
): KbFolderListing<T> {
  const prefix = currentPath ? `${normalizeRelativePath(currentPath)}/` : '';
  const folderSet = new Set<string>();
  const files: T[] = [];

  for (const doc of docs) {
    const rel = getDocRelativePath(doc);
    if (!rel) continue;

    if (currentPath) {
      if (rel === normalizeRelativePath(currentPath)) {
        files.push(doc);
        continue;
      }
      if (!rel.startsWith(prefix)) continue;
      const rest = rel.slice(prefix.length);
      const slash = rest.indexOf('/');
      if (slash >= 0) {
        const name = rest.slice(0, slash);
        if (name) folderSet.add(name);
      } else if (rest) {
        files.push(doc);
      }
    } else {
      const slash = rel.indexOf('/');
      if (slash >= 0) {
        const name = rel.slice(0, slash);
        if (name) folderSet.add(name);
      } else {
        files.push(doc);
      }
    }
  }

  return {
    folders: Array.from(folderSet).sort((a, b) =>
      a.localeCompare(b, 'ru', { sensitivity: 'base' }),
    ),
    files,
  };
}

/** Поиск по имени файла или полному relative_path. */
export function filterKbDocsByQuery<T extends KbDocPathRef>(
  docs: readonly T[],
  query: string,
): T[] {
  const q = query.trim().toLowerCase();
  if (!q) return [...docs];
  return docs.filter((doc) => {
    const rel = getDocRelativePath(doc).toLowerCase();
    const name = String(doc.filename || '').toLowerCase();
    return rel.includes(q) || name.includes(q);
  });
}
