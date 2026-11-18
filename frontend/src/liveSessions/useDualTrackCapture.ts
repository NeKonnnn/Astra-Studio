/**
 * Dual-track захват без join-бота:
 * - local: микрофон (getUserMedia)
 * - remote: системный / вкладка (getDisplayMedia + systemAudio)
 *
 * Важно: timeslice-фрагменты MediaRecorder — НЕ самостоятельные файлы
 * (только первый кусок имеет заголовок webm). Живой STT идёт через PCM→WAV.
 * MediaRecorder копится целиком только для финального архива.
 */
import { useCallback, useRef, useState } from 'react';
import { pcmFloat32ToWav16k } from '../utils/dictationAudio';
import { uploadChunk } from './api';
import type { LiveSegment } from './types';

export type CapturePhase = 'idle' | 'requesting' | 'live' | 'paused' | 'stopping';

export interface DualTrackLevels {
  local: number;
  remote: number;
}

export interface UseDualTrackCaptureOptions {
  language?: string;
  chunkMs?: number;
  jargon?: string[];
  autoStopOnCaptureEnd?: boolean;
  remoteSilenceSec?: number;
  onSegment?: (seg: LiveSegment) => void;
  onError?: (message: string) => void;
  onWarn?: (message: string) => void;
  onAutoStop?: (reason: 'capture_ended' | 'remote_silence') => void;
}

type PcmTap = {
  processor: ScriptProcessorNode;
  source: MediaStreamAudioSourceNode;
  chunks: Float32Array[];
  samples: number;
  track: 'local' | 'remote';
  muted: boolean;
};

function pickMime(): string {
  const candidates = [
    'audio/webm;codecs=opus',
    'audio/webm',
    'audio/ogg;codecs=opus',
  ];
  for (const c of candidates) {
    if (typeof MediaRecorder !== 'undefined' && MediaRecorder.isTypeSupported(c)) {
      return c;
    }
  }
  return '';
}

function rmsFromAnalyser(analyser: AnalyserNode): number {
  const buf = new Uint8Array(analyser.fftSize);
  analyser.getByteTimeDomainData(buf);
  let sum = 0;
  for (let i = 0; i < buf.length; i += 1) {
    const v = ((buf[i] ?? 128) - 128) / 128;
    sum += v * v;
  }
  return Math.sqrt(sum / Math.max(1, buf.length));
}

function concatFloat32(chunks: Float32Array[], total: number): Float32Array {
  const out = new Float32Array(total);
  let off = 0;
  for (const c of chunks) {
    out.set(c, off);
    off += c.length;
  }
  return out;
}

export function useDualTrackCapture(options: UseDualTrackCaptureOptions = {}) {
  const chunkMs = options.chunkMs ?? 3000;
  const language = options.language ?? 'auto';

  const [phase, setPhase] = useState<CapturePhase>('idle');
  const [levels, setLevels] = useState<DualTrackLevels>({ local: 0, remote: 0 });
  const [elapsedMs, setElapsedMs] = useState(0);
  const [hasRemote, setHasRemote] = useState(false);

  const sessionIdRef = useRef<string | null>(null);
  const localStreamRef = useRef<MediaStream | null>(null);
  const remoteStreamRef = useRef<MediaStream | null>(null);
  const localRecRef = useRef<MediaRecorder | null>(null);
  const remoteRecRef = useRef<MediaRecorder | null>(null);
  const localChunksRef = useRef<Blob[]>([]);
  const remoteChunksRef = useRef<Blob[]>([]);
  const startedAtRef = useRef<number>(0);
  const pausedAccumRef = useRef<number>(0);
  const pauseStartedRef = useRef<number | null>(null);
  const tickRef = useRef<number | null>(null);
  const levelRef = useRef<number | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const localAnalyserRef = useRef<AnalyserNode | null>(null);
  const remoteAnalyserRef = useRef<AnalyserNode | null>(null);
  const pcmTapsRef = useRef<PcmTap[]>([]);
  const uploadQueueRef = useRef<Promise<void>>(Promise.resolve());
  const remoteSilentSinceRef = useRef<number | null>(null);
  const chunkMsRef = useRef(chunkMs);
  chunkMsRef.current = chunkMs;
  const optionsRef = useRef(options);
  optionsRef.current = options;

  const clearTimers = () => {
    if (tickRef.current != null) {
      window.clearInterval(tickRef.current);
      tickRef.current = null;
    }
    if (levelRef.current != null) {
      window.clearInterval(levelRef.current);
      levelRef.current = null;
    }
  };

  const stopTracks = (stream: MediaStream | null) => {
    stream?.getTracks().forEach((t) => {
      try {
        t.stop();
      } catch {
        /* ignore */
      }
    });
  };

  const disconnectPcmTaps = () => {
    for (const tap of pcmTapsRef.current) {
      try {
        tap.processor.onaudioprocess = null;
        tap.processor.disconnect();
        tap.source.disconnect();
      } catch {
        /* ignore */
      }
    }
    pcmTapsRef.current = [];
  };

  const enqueueWav = useCallback((track: 'local' | 'remote', wav: Blob, offsetMs: number) => {
    const sessionId = sessionIdRef.current;
    if (!sessionId || !wav.size) return;
    const opts = optionsRef.current;
    uploadQueueRef.current = uploadQueueRef.current
      .then(async () => {
        const res = await uploadChunk({
          sessionId,
          track,
          offsetMs: Math.max(0, offsetMs),
          blob: wav,
          language: opts.language ?? language,
          jargon: opts.jargon,
        });
        if (res.segment && opts.onSegment) {
          opts.onSegment(res.segment as LiveSegment);
        }
      })
      .catch((err) => {
        opts.onError?.(
          err instanceof Error ? err.message : 'Ошибка живой стенограммы',
        );
      });
  }, [language]);

  const flushPcmTap = useCallback((tap: PcmTap, ctx: AudioContext, force = false) => {
    // На обычном flush ждём ≥0.6с речи; на стопе — ≥0.2с
    const minSec = force ? 0.2 : 0.6;
    if (tap.samples < ctx.sampleRate * minSec) {
      if (force) {
        tap.chunks = [];
        tap.samples = 0;
      }
      return;
    }
    const pcm = concatFloat32(tap.chunks, tap.samples);
    // Перекрытие ~0.8с: следующий кусок «видит» хвост фразы → STT реже обрезает
    const overlap = Math.min(
      Math.floor(ctx.sampleRate * 0.8),
      Math.max(0, pcm.length - Math.floor(ctx.sampleRate * 0.4)),
    );
    if (!force && overlap > 0) {
      tap.chunks = [pcm.slice(pcm.length - overlap)];
      tap.samples = overlap;
    } else {
      tap.chunks = [];
      tap.samples = 0;
    }
    const wav = pcmFloat32ToWav16k(pcm, ctx.sampleRate);
    if (!wav) return;
    const elapsed =
      Date.now() -
      startedAtRef.current -
      pausedAccumRef.current -
      (pauseStartedRef.current ? Date.now() - pauseStartedRef.current : 0);
    const durationMs = Math.round((pcm.length / ctx.sampleRate) * 1000);
    const offsetMs = Math.max(0, elapsed - durationMs);
    enqueueWav(tap.track, wav, offsetMs);
  }, [enqueueWav]);

  const wireArchiveRecorder = (
    stream: MediaStream,
    archiveRef: { current: Blob[] },
  ): MediaRecorder => {
    const mime = pickMime();
    const rec = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
    rec.ondataavailable = (ev) => {
      if (ev.data?.size) archiveRef.current.push(ev.data);
    };
    // Без timeslice для STT — один непрерывный поток в архив, куски только при stop
    rec.start();
    return rec;
  };

  const wirePcmTap = (
    ctx: AudioContext,
    stream: MediaStream,
    track: 'local' | 'remote',
  ): PcmTap => {
    const source = ctx.createMediaStreamSource(stream);
    // 4096 — совместимый буфер; ScriptProcessor deprecated, но для PCM-чанков надёжен
    const processor = ctx.createScriptProcessor(4096, 1, 1);
    const tap: PcmTap = {
      processor,
      source,
      chunks: [],
      samples: 0,
      track,
      muted: false,
    };
    const need = () => Math.floor((ctx.sampleRate * chunkMsRef.current) / 1000);

    processor.onaudioprocess = (ev) => {
      if (tap.muted || pauseStartedRef.current != null) return;
      const input = ev.inputBuffer.getChannelData(0);
      const copy = new Float32Array(input.length);
      copy.set(input);
      tap.chunks.push(copy);
      tap.samples += copy.length;
      if (tap.samples >= need()) {
        flushPcmTap(tap, ctx);
      }
    };

    source.connect(processor);
    // Chrome требует цепочку до destination, иначе onaudioprocess молчит.
    // gain=0 — чтобы микрофон не играл в динамики (иначе петля/эхо).
    const mute = ctx.createGain();
    mute.gain.value = 0;
    processor.connect(mute);
    mute.connect(ctx.destination);
    return tap;
  };

  const startLevelMeter = () => {
    let lastLocal = -1;
    let lastRemote = -1;
    levelRef.current = window.setInterval(() => {
      const local = localAnalyserRef.current
        ? rmsFromAnalyser(localAnalyserRef.current)
        : 0;
      const remote = remoteAnalyserRef.current
        ? rmsFromAnalyser(remoteAnalyserRef.current)
        : 0;
      const l = Math.round(local * 40) / 40;
      const r = Math.round(remote * 40) / 40;
      if (l !== lastLocal || r !== lastRemote) {
        lastLocal = l;
        lastRemote = r;
        setLevels({ local: l, remote: r });
      }

      const silenceSec = optionsRef.current.remoteSilenceSec ?? 0;
      if (silenceSec > 0 && remoteAnalyserRef.current && pauseStartedRef.current == null) {
        if (remote < 0.01) {
          if (remoteSilentSinceRef.current == null) {
            remoteSilentSinceRef.current = Date.now();
          } else if (Date.now() - remoteSilentSinceRef.current >= silenceSec * 1000) {
            remoteSilentSinceRef.current = null;
            optionsRef.current.onAutoStop?.('remote_silence');
          }
        } else {
          remoteSilentSinceRef.current = null;
        }
      }
    }, 200);
  };

  const start = useCallback(
    async (sessionId: string) => {
      if (phase === 'live' || phase === 'requesting') return;
      setPhase('requesting');
      sessionIdRef.current = sessionId;
      localChunksRef.current = [];
      remoteChunksRef.current = [];
      pausedAccumRef.current = 0;
      pauseStartedRef.current = null;
      disconnectPcmTaps();

      try {
        const local = await navigator.mediaDevices.getUserMedia({
          audio: {
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true,
          },
          video: false,
        });
        localStreamRef.current = local;

        let remote: MediaStream | null = null;
        try {
          remote = await navigator.mediaDevices.getDisplayMedia({
            video: true,
            audio: true,
            // Chromium: системный звук при шаринге всего экрана; звук вкладки — при tab + галочка
            systemAudio: 'include',
            preferCurrentTab: false,
          } as DisplayMediaStreamOptions & { systemAudio?: 'include' | 'exclude' });
          // displaySurface: window | monitor | browser — до остановки video-трека
          // (в DOM-lib MediaTrackSettings поле есть не во всех версиях TS)
          const surface = String(
            (remote.getVideoTracks()[0]?.getSettings?.() as { displaySurface?: string } | undefined)
              ?.displaySurface || '',
          ).toLowerCase();
          remote.getVideoTracks().forEach((t) => {
            t.stop();
            remote?.removeTrack(t);
          });
          if (!remote.getAudioTracks().length) {
            const tip =
              surface === 'window'
                ? 'Вы выбрали окно приложения — браузер не отдаёт из него звук. Нужна вкладка Chrome/Edge звонка с галочкой «Также транслировать звук вкладки» или весь экран с «Share system audio».'
                : surface === 'browser'
                  ? 'Вкладка выбрана, но без звука. В диалоге шаринга включите «Также транслировать звук вкладки» / Share tab audio (Chrome/Edge).'
                  : 'Системный звук не получен. Не шарьте окно Zoom/Teams — шарьте вкладку звонка со звуком или весь экран (Windows + Chrome).';
            optionsRef.current.onWarn?.(tip);
            stopTracks(remote);
            remote = null;
          } else {
            remote.getAudioTracks().forEach((t) => {
              t.addEventListener('ended', () => {
                optionsRef.current.onWarn?.(
                  'Захват экрана/звука остановлен (звонок или шаринг завершён).',
                );
                if (optionsRef.current.autoStopOnCaptureEnd !== false) {
                  optionsRef.current.onAutoStop?.('capture_ended');
                }
              });
            });
          }
        } catch {
          optionsRef.current.onWarn?.(
            'Не удалось захватить системный звук. Продолжаем только с микрофона.',
          );
          remote = null;
        }

        remoteStreamRef.current = remote;
        setHasRemote(Boolean(remote));

        const AudioCtx = window.AudioContext || (window as any).webkitAudioContext;
        if (!AudioCtx) {
          throw new Error('Web Audio API недоступен в этом браузере');
        }
        const ctx = new AudioCtx();
        if (ctx.state === 'suspended') await ctx.resume();
        audioCtxRef.current = ctx;

        const localSrc = ctx.createMediaStreamSource(local);
        const localAn = ctx.createAnalyser();
        localAn.fftSize = 512;
        localSrc.connect(localAn);
        localAnalyserRef.current = localAn;

        pcmTapsRef.current.push(wirePcmTap(ctx, local, 'local'));

        if (remote) {
          const remoteSrc = ctx.createMediaStreamSource(remote);
          const remoteAn = ctx.createAnalyser();
          remoteAn.fftSize = 512;
          remoteSrc.connect(remoteAn);
          remoteAnalyserRef.current = remoteAn;
          pcmTapsRef.current.push(wirePcmTap(ctx, remote, 'remote'));
        }

        localRecRef.current = wireArchiveRecorder(local, localChunksRef);
        if (remote) {
          remoteRecRef.current = wireArchiveRecorder(remote, remoteChunksRef);
        }

        startedAtRef.current = Date.now();
        setElapsedMs(0);
        tickRef.current = window.setInterval(() => {
          if (pauseStartedRef.current != null) return;
          setElapsedMs(
            Date.now() - startedAtRef.current - pausedAccumRef.current,
          );
        }, 250);
        startLevelMeter();
        setPhase('live');
      } catch (err) {
        setPhase('idle');
        optionsRef.current.onError?.(
          err instanceof Error
            ? err.message
            : 'Не удалось получить доступ к микрофону',
        );
        disconnectPcmTaps();
        stopTracks(localStreamRef.current);
        stopTracks(remoteStreamRef.current);
        localStreamRef.current = null;
        remoteStreamRef.current = null;
      }
    },
    [flushPcmTap, phase],
  );

  const pause = useCallback(() => {
    if (phase !== 'live') return;
    try {
      localRecRef.current?.pause();
      remoteRecRef.current?.pause();
    } catch {
      /* ignore */
    }
    for (const tap of pcmTapsRef.current) tap.muted = true;
    pauseStartedRef.current = Date.now();
    setPhase('paused');
  }, [phase]);

  const resume = useCallback(() => {
    if (phase !== 'paused') return;
    try {
      localRecRef.current?.resume();
      remoteRecRef.current?.resume();
    } catch {
      /* ignore */
    }
    for (const tap of pcmTapsRef.current) tap.muted = false;
    if (pauseStartedRef.current != null) {
      pausedAccumRef.current += Date.now() - pauseStartedRef.current;
      pauseStartedRef.current = null;
    }
    setPhase('live');
  }, [phase]);

  const stop = useCallback(async () => {
    if (phase === 'idle' || phase === 'stopping') {
      return {
        localBlob: null as Blob | null,
        remoteBlob: null as Blob | null,
        mixedBlob: null as Blob | null,
        durationMs: elapsedMs,
      };
    }
    setPhase('stopping');
    clearTimers();

    // дослать хвост PCM (force — без overlap)
    const ctx = audioCtxRef.current;
    if (ctx) {
      for (const tap of pcmTapsRef.current) {
        flushPcmTap(tap, ctx, true);
      }
    }
    disconnectPcmTaps();

    const waitStop = (rec: MediaRecorder | null) =>
      new Promise<void>((resolve) => {
        if (!rec || rec.state === 'inactive') {
          resolve();
          return;
        }
        rec.addEventListener('stop', () => resolve(), { once: true });
        try {
          if (rec.state === 'recording' || rec.state === 'paused') {
            rec.requestData?.();
          }
          rec.stop();
        } catch {
          resolve();
        }
      });

    await Promise.all([
      waitStop(localRecRef.current),
      waitStop(remoteRecRef.current),
    ]);
    await uploadQueueRef.current.catch(() => undefined);

    const mime = pickMime() || 'audio/webm';
    const localBlob = localChunksRef.current.length
      ? new Blob(localChunksRef.current, { type: mime })
      : null;
    const remoteBlob = remoteChunksRef.current.length
      ? new Blob(remoteChunksRef.current, { type: mime })
      : null;
    const mixedBlob = localBlob || remoteBlob;

    stopTracks(localStreamRef.current);
    stopTracks(remoteStreamRef.current);
    localStreamRef.current = null;
    remoteStreamRef.current = null;
    localRecRef.current = null;
    remoteRecRef.current = null;
    try {
      await audioCtxRef.current?.close();
    } catch {
      /* ignore */
    }
    audioCtxRef.current = null;
    localAnalyserRef.current = null;
    remoteAnalyserRef.current = null;

    const durationMs =
      Date.now() -
      startedAtRef.current -
      pausedAccumRef.current -
      (pauseStartedRef.current ? Date.now() - pauseStartedRef.current : 0);

    setPhase('idle');
    setLevels({ local: 0, remote: 0 });
    setHasRemote(false);
    return {
      localBlob,
      remoteBlob,
      mixedBlob,
      durationMs: Math.max(0, durationMs),
    };
  }, [elapsedMs, flushPcmTap, phase]);

  return {
    phase,
    levels,
    elapsedMs,
    hasRemote,
    start,
    pause,
    resume,
    stop,
  };
}
