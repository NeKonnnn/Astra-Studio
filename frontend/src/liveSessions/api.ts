import { authFetch, getApiUrl } from '../config/api';
import type { LiveCollection, LiveSessionCard, LiveSessionDetail } from './types';

const BASE = '/api/live-sessions';

async function parseJson<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export async function listSessions(params?: {
  q?: string;
  trashed?: boolean;
  collection_id?: string;
  limit?: number;
}): Promise<LiveSessionCard[]> {
  const qs = new URLSearchParams();
  if (params?.q) qs.set('q', params.q);
  if (params?.trashed) qs.set('trashed', 'true');
  if (params?.collection_id) qs.set('collection_id', params.collection_id);
  if (params?.limit) qs.set('limit', String(params.limit));
  const url = `${BASE}${qs.toString() ? `?${qs}` : ''}`;
  const data = await parseJson<{ items: LiveSessionCard[] }>(
    await authFetch(getApiUrl(url)),
  );
  return data.items || [];
}

export async function getSession(id: string): Promise<LiveSessionDetail> {
  return parseJson(await authFetch(getApiUrl(`${BASE}/${encodeURIComponent(id)}`)));
}

export async function createSession(body?: {
  title?: string;
  language?: string;
  source_app?: string;
  participants?: string[];
}): Promise<LiveSessionDetail> {
  return parseJson(
    await authFetch(getApiUrl(BASE), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    }),
  );
}

export async function updateSession(
  id: string,
  body: Record<string, unknown>,
): Promise<LiveSessionDetail> {
  return parseJson(
    await authFetch(getApiUrl(`${BASE}/${encodeURIComponent(id)}`), {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  );
}

export async function startSession(id: string): Promise<LiveSessionDetail> {
  return parseJson(
    await authFetch(getApiUrl(`${BASE}/${encodeURIComponent(id)}/start`), {
      method: 'POST',
    }),
  );
}

export async function pauseSession(id: string): Promise<LiveSessionDetail> {
  return parseJson(
    await authFetch(getApiUrl(`${BASE}/${encodeURIComponent(id)}/pause`), {
      method: 'POST',
    }),
  );
}

export async function resumeSession(id: string): Promise<LiveSessionDetail> {
  return parseJson(
    await authFetch(getApiUrl(`${BASE}/${encodeURIComponent(id)}/resume`), {
      method: 'POST',
    }),
  );
}

export interface LiveSessionPrefs {
  echo_detect: boolean;
  auto_stop_on_capture_end: boolean;
  auto_stop_on_remote_silence_sec: number;
  jargon: string[];
  show_echo_segments: boolean;
}

export async function getPrefs(): Promise<LiveSessionPrefs> {
  return parseJson(await authFetch(getApiUrl(`${BASE}/prefs`)));
}

export async function savePrefs(
  patch: Partial<Omit<LiveSessionPrefs, 'jargon'>> & { jargon?: string[] | string },
): Promise<LiveSessionPrefs> {
  return parseJson(
    await authFetch(getApiUrl(`${BASE}/prefs`), {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    }),
  );
}

export async function uploadChunk(params: {
  sessionId: string;
  track: 'local' | 'remote' | 'mixed';
  offsetMs: number;
  blob: Blob;
  language?: string;
  jargon?: string[];
}): Promise<{ text: string; segment?: LiveSessionDetail['segments'][number] | null; is_echo?: boolean }> {
  const fd = new FormData();
  fd.append('file', params.blob, `${params.track}.wav`);
  fd.append('track', params.track);
  fd.append('offset_ms', String(params.offsetMs));
  if (params.language) fd.append('language', params.language);
  if (params.jargon?.length) fd.append('jargon', params.jargon.join(', '));
  return parseJson(
    await authFetch(
      getApiUrl(`${BASE}/${encodeURIComponent(params.sessionId)}/chunks`),
      { method: 'POST', body: fd },
    ),
  );
}

export async function finalizeSession(params: {
  sessionId: string;
  localBlob?: Blob | null;
  remoteBlob?: Blob | null;
  mixedBlob?: Blob | null;
  durationMs: number;
  generateBrief?: boolean;
}): Promise<LiveSessionDetail> {
  const fd = new FormData();
  fd.append('duration_ms', String(params.durationMs));
  fd.append('generate_brief', params.generateBrief === false ? 'false' : 'true');
  if (params.localBlob) fd.append('local_audio', params.localBlob, 'local.webm');
  if (params.remoteBlob) fd.append('remote_audio', params.remoteBlob, 'remote.webm');
  if (params.mixedBlob) fd.append('mixed_audio', params.mixedBlob, 'mixed.webm');
  return parseJson(
    await authFetch(
      getApiUrl(`${BASE}/${encodeURIComponent(params.sessionId)}/finalize`),
      { method: 'POST', body: fd },
    ),
  );
}

export async function regenerateBrief(
  id: string,
  instruction?: string,
): Promise<LiveSessionDetail> {
  return parseJson(
    await authFetch(getApiUrl(`${BASE}/${encodeURIComponent(id)}/brief`), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ regenerate: true, instruction }),
    }),
  );
}

export async function renameSpeakers(
  id: string,
  mapping: Record<string, string>,
): Promise<LiveSessionDetail> {
  return parseJson(
    await authFetch(getApiUrl(`${BASE}/${encodeURIComponent(id)}/speakers`), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mapping }),
    }),
  );
}

export async function trashSession(id: string): Promise<void> {
  await parseJson(
    await authFetch(getApiUrl(`${BASE}/${encodeURIComponent(id)}/trash`), {
      method: 'POST',
    }),
  );
}

export async function restoreSession(id: string): Promise<void> {
  await parseJson(
    await authFetch(getApiUrl(`${BASE}/${encodeURIComponent(id)}/restore`), {
      method: 'POST',
    }),
  );
}

export async function deleteSession(id: string): Promise<void> {
  await parseJson(
    await authFetch(getApiUrl(`${BASE}/${encodeURIComponent(id)}`), {
      method: 'DELETE',
    }),
  );
}

export async function listCollections(): Promise<LiveCollection[]> {
  const data = await parseJson<{ items: LiveCollection[] }>(
    await authFetch(getApiUrl(`${BASE}/collections`)),
  );
  return data.items || [];
}

export async function createCollection(name: string): Promise<LiveCollection> {
  return parseJson(
    await authFetch(getApiUrl(`${BASE}/collections`), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    }),
  );
}
