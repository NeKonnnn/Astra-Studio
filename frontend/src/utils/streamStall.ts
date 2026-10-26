/** Нет чанков после начала стрима — часто finish_reason=length без chat_complete. */
export const STREAM_STALL_AFTER_CHUNK_MS = 120_000;
/** Нет вообще никакой активности (RAG/ожидание первого токена) — дольше. */
export const STREAM_STALL_BEFORE_CHUNK_MS = 360_000;
/** ComfyUI/Flux: первая загрузка чекпоинта часто 5–15 мин без чанков. */
export const STREAM_STALL_MEDIA_MS = 1_200_000;

/** Idle-лимит стрима: media-gen не должен обрываться коротким 2‑мин stall. */
export function resolveStreamStallLimitMs(opts: {
  mediaGeneration?: boolean;
  receivedStreamData?: boolean;
}): number {
  if (opts.mediaGeneration) return STREAM_STALL_MEDIA_MS;
  if (opts.receivedStreamData) return STREAM_STALL_AFTER_CHUNK_MS;
  return STREAM_STALL_BEFORE_CHUNK_MS;
}
