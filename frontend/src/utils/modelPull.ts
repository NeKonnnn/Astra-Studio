import { authFetch, getApiUrl } from '../config/api';

export type ParsedPullInput = {
  repo_id?: string;
  url?: string;
  filename?: string;
  catalogHint?: string;
};

export type CatalogPullHit = {
  id: string;
  display_name?: string;
  local_name?: string;
  source?: { repo_id?: string };
};

/** Разбор ввода: org/repo, hf://…, hf download …, hf.co/…, URL, catalog id */
export function parsePullInput(raw: string): ParsedPullInput {
  let s = raw.trim().replace(/^["']|["']$/g, '');
  if (!s) return {};

  s = s
    .replace(/^huggingface-cli\s+download\s+/i, '')
    .replace(/^hf\s+download\s+/i, '')
    .trim();

  if (/^https?:\/\//i.test(s)) {
    try {
      const u = new URL(s);
      const parts = u.pathname.split('/').filter(Boolean);
      if (u.hostname.includes('huggingface.co') && parts.length >= 2) {
        const repo_id = `${parts[0]}/${parts[1]}`;
        const resolveIdx = parts.indexOf('resolve');
        const blobIdx = parts.indexOf('blob');
        const idx = resolveIdx >= 0 ? resolveIdx : blobIdx;
        const filename = idx >= 0 && parts[idx + 2] ? parts.slice(idx + 2).join('/') : undefined;
        return { repo_id, filename };
      }
    } catch {
      /* fallthrough */
    }
    return { url: s };
  }

  s = s.replace(/^hf:\/\//i, '').replace(/^hf:/i, '');
  let t = s
    .replace(/^hf\.co\//i, '')
    .replace(/^huggingface\.co\//i, '')
    .trim();

  const colon = t.indexOf(':');
  if (colon > 0 && t.slice(0, colon).includes('/')) {
    const repo = t.slice(0, colon);
    const quant = t.slice(colon + 1);
    const segs = repo.split('/').filter(Boolean);
    if (segs.length >= 2) {
      return {
        repo_id: `${segs[0]}/${segs[1]}`,
        filename: quant.includes('.') ? quant : undefined,
        catalogHint: quant,
      };
    }
  }

  if (t.includes('/')) {
    const segs = t.split('/').filter(Boolean);
    if (segs.length >= 2) {
      const repo_id = `${segs[0]}/${segs[1]}`;
      const rest = segs.slice(2).join('/');
      if (!rest && /\s+\S+\.\w+$/.test(t)) {
        const [repoPart, filePart] = t.split(/\s+/, 2);
        const rs = repoPart.split('/').filter(Boolean);
        if (rs.length >= 2 && filePart) {
          return { repo_id: `${rs[0]}/${rs[1]}`, filename: filePart };
        }
      }
      if (rest) return { repo_id, filename: rest };
      return { repo_id };
    }
  }

  const spaceParts = t.split(/\s+/).filter(Boolean);
  if (spaceParts.length >= 2 && spaceParts[0].includes('/')) {
    const segs = spaceParts[0].split('/').filter(Boolean);
    if (segs.length >= 2) {
      return { repo_id: `${segs[0]}/${segs[1]}`, filename: spaceParts[1] };
    }
  }

  return { catalogHint: t };
}

async function readErrorDetail(res: Response, fallback: string): Promise<string> {
  const data = await res.json().catch(() => ({} as { detail?: string }));
  return (typeof data.detail === 'string' && data.detail) || fallback;
}

export type StartPullResult =
  | { ok: true; jobId?: string; message: string }
  | { ok: false; error: string };

/**
 * Запуск Pull: catalog match → custom HF/URL.
 * Для чата по умолчанию type=llm_gguf (если не выведен из имени файла).
 */
export async function startModelPull(opts: {
  query: string;
  modelType?: string;
  catalog?: CatalogPullHit[];
}): Promise<StartPullResult> {
  const raw = opts.query.trim();
  if (!raw) return { ok: false, error: 'Укажите ссылку или id модели' };
  const modelType = opts.modelType || 'llm_gguf';
  const catalog = opts.catalog || [];

  const lower = raw.toLowerCase();
  const hit = catalog.find(
    (m) =>
      m.id.toLowerCase() === lower
      || (m.display_name || '').toLowerCase() === lower
      || (m.source?.repo_id || '').toLowerCase() === lower
      || (m.local_name || '').toLowerCase() === lower,
  );
  if (hit) {
    const res = await authFetch(getApiUrl('/api/model-downloads/start'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ catalog_id: hit.id, apply: true }),
    });
    if (!res.ok) return { ok: false, error: await readErrorDetail(res, `Ошибка ${res.status}`) };
    const data = await res.json().catch(() => ({} as { id?: string }));
    return { ok: true, jobId: data.id, message: 'Загрузка запущена' };
  }

  const parsed = parsePullInput(raw);
  if (parsed.catalogHint && !parsed.repo_id && !parsed.url) {
    const soft = catalog.find(
      (m) =>
        m.id.toLowerCase().includes(parsed.catalogHint!.toLowerCase())
        || (m.local_name || '').toLowerCase().includes(parsed.catalogHint!.toLowerCase()),
    );
    if (soft) {
      const res = await authFetch(getApiUrl('/api/model-downloads/start'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ catalog_id: soft.id, apply: true }),
      });
      if (!res.ok) return { ok: false, error: await readErrorDetail(res, `Ошибка ${res.status}`) };
      const data = await res.json().catch(() => ({} as { id?: string }));
      return { ok: true, jobId: data.id, message: 'Загрузка запущена' };
    }
  }

  const inferredType =
    (parsed.filename || '').toLowerCase().endsWith('.gguf')
    || (parsed.repo_id || '').toLowerCase().includes('gguf')
      ? 'llm_gguf'
      : modelType;

  const body: Record<string, unknown> = {
    type: inferredType,
    apply: true,
  };
  if (parsed.repo_id) body.repo_id = parsed.repo_id;
  if (parsed.url) body.url = parsed.url;
  if (parsed.filename) {
    body.filename = parsed.filename;
    body.local_name = parsed.filename.split('/').pop();
  }
  if (parsed.repo_id && !parsed.filename && inferredType === 'llm_gguf') {
    const filesRes = await authFetch(
      getApiUrl(`/api/model-downloads/hf-files?repo_id=${encodeURIComponent(parsed.repo_id)}`),
    );
    if (filesRes.ok) {
      const filesData = await filesRes.json();
      const gguf: string[] = Array.isArray(filesData.gguf) ? filesData.gguf : [];
      if (gguf.length === 1) {
        body.filename = gguf[0];
        body.local_name = gguf[0].split('/').pop();
      } else if (gguf.length > 1) {
        return {
          ok: false,
          error: `В репозитории несколько GGUF (${gguf.length}). Укажите файл, например: ${parsed.repo_id}/${gguf[0]}`,
        };
      }
    }
  }
  if (inferredType === 'stt') body.stt_size = 'medium';
  if (String(inferredType).startsWith('rag') && parsed.repo_id) {
    body.local_name = parsed.repo_id.split('/').pop();
  }

  if (!body.repo_id && !body.url) {
    return {
      ok: false,
      error: 'Не удалось разобрать модель. Пример: unsloth/Qwen3.5-4B-GGUF/…gguf',
    };
  }

  const res = await authFetch(getApiUrl('/api/model-downloads/start'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) return { ok: false, error: await readErrorDetail(res, `Ошибка ${res.status}`) };
  const data = await res.json().catch(() => ({} as { id?: string }));
  return { ok: true, jobId: data.id, message: 'Загрузка запущена' };
}
