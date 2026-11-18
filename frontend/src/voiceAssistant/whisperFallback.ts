import { authFetch, getApiUrl, API_ENDPOINTS } from '../config/api';

/**
 * Fallback STT через WhisperX: короткая запись MediaRecorder → /api/voice/recognize.
 * Используется только если Web Speech API недоступен.
 */
export class WhisperFallbackListener {
  private stream: MediaStream | null = null;
  private recorder: MediaRecorder | null = null;
  private chunks: BlobPart[] = [];
  private active = false;
  private onText: (text: string) => void;
  private onError: (error: string) => void;
  private chunkTimer: ReturnType<typeof setInterval> | null = null;

  constructor(onText: (text: string) => void, onError: (error: string) => void) {
    this.onText = onText;
    this.onError = onError;
  }

  async start(): Promise<boolean> {
    if (this.active) return true;
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mime = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
        ? 'audio/webm;codecs=opus'
        : 'audio/webm';
      this.recorder = new MediaRecorder(this.stream, { mimeType: mime });
      this.chunks = [];
      this.recorder.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) this.chunks.push(e.data);
      };
      this.recorder.onstop = () => {
        void this.flushChunk();
      };
      this.active = true;
      this.recorder.start();
      // Каждые ~2.5с режем чанк и распознаём (псевдо continuous)
      this.chunkTimer = setInterval(() => {
        if (!this.active || !this.recorder || this.recorder.state !== 'recording') return;
        this.recorder.stop();
        try {
          this.recorder.start();
        } catch {
          /* ignore */
        }
      }, 2500);
      return true;
    } catch (e) {
      this.onError(e instanceof Error ? e.message : 'Нет доступа к микрофону');
      this.stop();
      return false;
    }
  }

  stop(): void {
    this.active = false;
    if (this.chunkTimer) {
      clearInterval(this.chunkTimer);
      this.chunkTimer = null;
    }
    try {
      if (this.recorder && this.recorder.state !== 'inactive') this.recorder.stop();
    } catch {
      /* ignore */
    }
    this.recorder = null;
    this.stream?.getTracks().forEach((t) => t.stop());
    this.stream = null;
    this.chunks = [];
  }

  private async flushChunk(): Promise<void> {
    if (!this.chunks.length) return;
    const blob = new Blob(this.chunks, { type: 'audio/webm' });
    this.chunks = [];
    if (blob.size < 800) return;
    try {
      const form = new FormData();
      form.append('file', blob, 'voice.webm');
      const resp = await authFetch(getApiUrl(API_ENDPOINTS.VOICE_RECOGNIZE), {
        method: 'POST',
        body: form,
      });
      if (!resp.ok) {
        this.onError('Ошибка WhisperX');
        return;
      }
      const data = (await resp.json()) as { text?: string; transcript?: string };
      const text = String(data.text || data.transcript || '').trim();
      if (text) this.onText(text);
    } catch (e) {
      this.onError(e instanceof Error ? e.message : 'Ошибка распознавания');
    }
  }
}
