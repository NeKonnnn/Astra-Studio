export type SpeechRecognitionHandlers = {
  onPartial?: (text: string) => void;
  onFinal?: (text: string) => void;
  onError?: (error: string) => void;
  onStart?: () => void;
  onEnd?: () => void;
};

type BrowserSpeechRecognition = {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  maxAlternatives: number;
  onresult: ((event: SpeechRecognitionEventLike) => void) | null;
  onerror: ((event: { error?: string }) => void) | null;
  onstart: (() => void) | null;
  onend: (() => void) | null;
  start: () => void;
  stop: () => void;
  abort: () => void;
};

type SpeechRecognitionEventLike = {
  resultIndex: number;
  results: ArrayLike<{
    isFinal: boolean;
    0: { transcript: string };
  }>;
};

type SpeechRecognitionCtor = new () => BrowserSpeechRecognition;

function getSpeechRecognitionCtor(): SpeechRecognitionCtor | null {
  if (typeof window === 'undefined') return null;
  const w = window as Window & {
    SpeechRecognition?: SpeechRecognitionCtor;
    webkitSpeechRecognition?: SpeechRecognitionCtor;
  };
  return w.SpeechRecognition || w.webkitSpeechRecognition || null;
}

export function isWebSpeechAvailable(): boolean {
  return Boolean(getSpeechRecognitionCtor());
}

/**
 * Непрерывный слушатель Web Speech API с авто-рестартом.
 * Chrome часто останавливает continuous-сессию — перезапускаем, пока active.
 */
export class ContinuousSpeechRecognition {
  private recognition: BrowserSpeechRecognition | null = null;
  private active = false;
  private restartTimer: ReturnType<typeof setTimeout> | null = null;
  private language: string;
  private handlers: SpeechRecognitionHandlers;

  constructor(language: string, handlers: SpeechRecognitionHandlers = {}) {
    this.language = language;
    this.handlers = handlers;
  }

  setHandlers(handlers: SpeechRecognitionHandlers): void {
    this.handlers = handlers;
  }

  setLanguage(language: string): void {
    this.language = language;
    if (this.recognition) this.recognition.lang = language;
  }

  start(): boolean {
    const Ctor = getSpeechRecognitionCtor();
    if (!Ctor) {
      this.handlers.onError?.('Web Speech API недоступен');
      return false;
    }
    this.active = true;
    if (!this.recognition) {
      this.recognition = new Ctor();
      this.recognition.continuous = true;
      this.recognition.interimResults = true;
      this.recognition.maxAlternatives = 1;
      this.recognition.lang = this.language;

      this.recognition.onstart = () => this.handlers.onStart?.();
      this.recognition.onend = () => {
        this.handlers.onEnd?.();
        if (this.active) this.scheduleRestart();
      };
      this.recognition.onerror = (event) => {
        const err = event?.error || 'unknown';
        // no-speech / aborted — нормальны для continuous; не эскалируем
        if (err === 'no-speech' || err === 'aborted') return;
        this.handlers.onError?.(err);
        if (err === 'not-allowed' || err === 'service-not-allowed') {
          this.active = false;
        }
      };
      this.recognition.onresult = (event) => {
        let interim = '';
        let finalText = '';
        for (let i = event.resultIndex; i < event.results.length; i += 1) {
          const result = event.results[i];
          const transcript = result?.[0]?.transcript || '';
          if (result.isFinal) finalText += `${transcript} `;
          else interim += `${transcript} `;
        }
        const partial = (interim || finalText).trim();
        if (partial) this.handlers.onPartial?.(partial);
        const finalized = finalText.trim();
        if (finalized) this.handlers.onFinal?.(finalized);
      };
    }
    try {
      this.recognition.start();
      return true;
    } catch {
      this.scheduleRestart();
      return true;
    }
  }

  stop(): void {
    this.active = false;
    if (this.restartTimer) {
      clearTimeout(this.restartTimer);
      this.restartTimer = null;
    }
    try {
      this.recognition?.stop();
    } catch {
      /* ignore */
    }
  }

  abort(): void {
    this.active = false;
    if (this.restartTimer) {
      clearTimeout(this.restartTimer);
      this.restartTimer = null;
    }
    try {
      this.recognition?.abort();
    } catch {
      /* ignore */
    }
  }

  private scheduleRestart(): void {
    if (!this.active) return;
    if (this.restartTimer) clearTimeout(this.restartTimer);
    this.restartTimer = setTimeout(() => {
      this.restartTimer = null;
      if (!this.active || !this.recognition) return;
      try {
        this.recognition.start();
      } catch {
        this.scheduleRestart();
      }
    }, 250);
  }
}
