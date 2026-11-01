import { flushSync } from 'react-dom';

export interface RagPendingUpload {
  clientId: string;
  filename: string;
  size: number;
}

export function createRagPendingUploads(files: File[]): RagPendingUpload[] {
  return files.map((file) => ({
    clientId: `${Date.now()}-${Math.random().toString(36).slice(2)}-${file.name}`,
    filename: file.name,
    size: file.size,
  }));
}

export function removeRagPendingUploads(
  pending: RagPendingUpload[],
  clientIds: string[],
): RagPendingUpload[] {
  if (!clientIds.length) return pending;
  const drop = new Set(clientIds);
  return pending.filter((item) => !drop.has(item.clientId));
}

export function getRagFileTypeLabel(filename: string): string {
  const ext = filename.split('.').pop()?.toLowerCase() || '';
  if (ext === 'pdf') return 'PDF';
  if (['docx', 'doc', 'docm'].includes(ext)) return 'Word';
  if (['xlsx', 'xls', 'xlsm', 'csv'].includes(ext)) return 'Excel';
  if (['txt', 'md', 'log'].includes(ext)) return 'TXT';
  if (ext === 'rtf') return 'RTF';
  if (['jpg', 'jpeg', 'png', 'webp','bmp'].includes(ext)) return 'Image (OCR)';
  return 'File';
}

/** id документа из ответа POST upload (KB / project / memory RAG). */
export function parseRagUploadDocumentId(payload: unknown): number | null {
  if (payload == null) return null;
  if (typeof payload === 'number' && Number.isFinite(payload) && payload > 0) {
    return payload;
  }
  if (typeof payload !== 'object') return null;

  const record = payload as Record<string, unknown>;
  for (const key of ['document_id', 'documentId', 'id'] as const) {
    const value = record[key];
    if (value == null || value === '') continue;
    const parsed = Number(value);
    if (Number.isFinite(parsed) && parsed > 0) return parsed;
  }

  for (const nestedKey of ['document', 'data', 'result'] as const) {
    const nested = record[nestedKey];
    if (nested != null) {
      const nestedId = parseRagUploadDocumentId(nested);
      if (nestedId != null) return nestedId;
    }
  }

  return null;
}

const RAG_DUPLICATE_FALLBACK =
  'Упс... Такой документ уже подгружен в базу';

/** Достаёт человекочитаемый detail из ответа FastAPI. */
export function formatRagApiDetail(detail: unknown, fallback: string): string {
  if (typeof detail === 'string' && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d) =>
        typeof d === 'object' && d && 'msg' in d
          ? String((d as { msg: unknown }).msg)
          : String(d),
      )
      .join('; ');
  }
  if (detail && typeof detail === 'object') {
    const rec = detail as Record<string, unknown>;
    if (typeof rec.message === 'string' && rec.message.trim()) return rec.message;
    if (typeof rec.msg === 'string' && rec.msg.trim()) return rec.msg;
    if (typeof rec.detail === 'string' && rec.detail.trim()) return rec.detail;
  }
  return fallback;
}

/**
 * Дубликат документа: 409 + code=duplicate_document (или текст «уже подгружен»).
 * Возвращает текст для warning Alert или null.
 */
export function parseRagDuplicateUploadMessage(
  status: number,
  payload: unknown,
  filename?: string,
): string | null {
  if (status !== 409 && status !== 422) return null;

  const detail =
    payload && typeof payload === 'object' && 'detail' in payload
      ? (payload as { detail: unknown }).detail
      : payload;

  const isDuplicateObject =
    detail &&
    typeof detail === 'object' &&
    String((detail as { code?: unknown }).code || '') === 'duplicate_document';

  const text = formatRagApiDetail(detail, '');
  const looksLikeDuplicate =
    isDuplicateObject ||
    /уже подгружен в базу/i.test(text) ||
    /duplicate_document/i.test(text);

  if (!looksLikeDuplicate) return null;

  const message = text.trim() || RAG_DUPLICATE_FALLBACK;
  if (filename && !message.includes(filename)) {
    return `«${filename}»: ${message}`;
  }
  return message;
}

/** Стили оранжевой warning-рамки (как в ProjectRag / AgentConstructor). */
export const ragWarningAlertSx = {
  mb: 2,
  bgcolor: 'rgba(255, 152, 0, 0.12)',
  color: '#ffb74d',
  border: '1px solid rgba(255, 152, 0, 0.35)',
  '& .MuiAlert-icon': { color: '#ff9800' },
} as const;

/** Объединить списки документов по id (серверные поля перекрывают локальные). */
export function mergeRagDocumentsById<T extends { id: number }>(
  prev: T[],
  incoming: T[],
): T[] {
  const byId = new Map<number, T>();
  for (const doc of prev) byId.set(doc.id, doc);
  for (const doc of incoming) byId.set(doc.id, doc);
  return Array.from(byId.values());
}

/** Сразу отрисовать UI после загрузки одного файла (не ждать конца пакета). */
export function commitRagUploadUiUpdate(update: () => void): void {
  flushSync(update);
}

/** Сколько RAG-файлов индексировать одновременно (не перегружать embed-сервис). */
export const RAG_UPLOAD_CONCURRENCY = 3;

/**
 * Параллельный обход с ограничением concurrency.
 * Нужен, чтобы пакет из N документов не ждал сумму всех индексаций подряд.
 */
export async function mapWithConcurrency<T, R>(
  items: T[],
  concurrency: number,
  worker: (item: T, index: number) => Promise<R>,
): Promise<R[]> {
  const results = new Array<R>(items.length);
  if (!items.length) return results;
  let nextIndex = 0;
  const limit = Math.max(1, Math.min(concurrency, items.length));
  const runners = Array.from({ length: limit }, async () => {
    while (true) {
      const index = nextIndex;
      nextIndex += 1;
      if (index >= items.length) return;
      results[index] = await worker(items[index], index);
    }
  });
  await Promise.all(runners);
  return results;
}

/** SHA-256 файла в hex. Нужен только чтобы сравнить файлы пачки между собой. */
async function fileContentHash(file: File): Promise<string> {
  const buf = await file.arrayBuffer();
  const digest = await crypto.subtle.digest('SHA-256', buf);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('');
}

/**

* Схлопнуть одинаковые по содержимому файлы ВНУТРИ одной загрузки.
* 
* Сервер грузит по RAG_UPLOAD_CONCURRENCY файлов сразу, а проверка дубликата
* и вставка там не атомарны: параллельные запросы успевают увидеть пустую
* базу до того, как первый из них запишется. Сколько копий проскочит -
* зависит от таймингов, поэтому результат выглядел случайным.
* 
* Убираем повторы до отправки: в параллель уходят только разные файлы.
* Межпакетные дубликаты по-прежнему ловит сервер - там гонки нет.
* 
* Сначала сравниваем размеры: файлы разной длины заведомо различны, и
* хэшировать (а значит читать целиком в память) нужно только совпавших по
* размеру. В обычной пачке таких нет вовсе.
* 
* Хэш посчитать не удалось (нет secure context для crypto.subtle, ошибка
* чтения) - файл идёт на загрузку как есть, поведение прежнее.
 */
export async function dedupeFilesByContent(
  files: File[],
): Promise<{ unique: File[]; skipped: string[] }> {
  if (files.length < 2) return { unique: files, skipped: [] };

  const sizeCount = new Map<number, number>();
  for (const f of files) {
    sizeCount.set(f.size, (sizeCount.get(f.size) || 0) + 1);
  }

  const seen = new Set<string>();
  const unique: File[] = [];
  const skipped: string[] = [];
  for (const file of files) {
    if ((sizeCount.get(file.size) || 0) < 2) {
      unique.push(file);
      continue;
    }
    let key: string | null = null;
    try {
      key = await fileContentHash(file);
    } catch {
      key = null;
    }
    if (key === null) {
      unique.push(file);
      continue;
    }
    if (seen.has(key)) {
      skipped.push(file.name);
      continue;
    }
    seen.add(key);
    unique.push(file);
  }
  return { unique, skipped };
}
