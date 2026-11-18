/**
 * Streaming TTS: озвучка по фразам + barge-in.
 * Пока идёт TTS — микрофон глушится (иначе echo → мгновенный stop и «тишина»).
 */

import { authFetch, getApiUrl, API_ENDPOINTS } from '../config/api';
import { loadVoiceAssistantSettings } from './settings';
import { vaLog, vaWarn } from './log';
import type { VoiceAssistantTtsEngine } from './types';

export type TtsVoiceOpts = {
  voice_id?: string;
  voice_speaker?: string;
  speech_rate?: number;
  engine?: VoiceAssistantTtsEngine;
  sileroAvailable?: boolean;
};

type QueueItem = { text: string; resolve: () => void };

let speaking = false;
let aborted = false;
let currentAudio: HTMLAudioElement | null = null;
let queue: QueueItem[] = [];
let pumping = false;
let micMutedUntil = 0;
const audioCache = new Map<string, string>();
let audioUnlocked = false;

type MicMuteListener = (muted: boolean) => void;
const micMuteListeners = new Set<MicMuteListener>();

export function onMicMuteChange(cb: MicMuteListener): () => void {
  micMuteListeners.add(cb);
  return () => micMuteListeners.delete(cb);
}

function setMicMuted(ms: number): void {
  micMutedUntil = Math.max(micMutedUntil, Date.now() + ms);
  micMuteListeners.forEach((cb) => cb(true));
}

export function isMicMutedForTts(): boolean {
  return Date.now() < micMutedUntil || speaking || pumping;
}

export function isLiveTtsSpeaking(): boolean {
  return speaking || pumping || queue.length > 0;
}

/** Разблокировка autoplay после жеста пользователя (вкл. VA). */
export async function unlockAudioPlayback(): Promise<void> {
  if (audioUnlocked || typeof window === 'undefined') return;
  try {
    const Ctx = window.AudioContext || (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (Ctx) {
      const ctx = new Ctx();
      if (ctx.state === 'suspended') await ctx.resume();
      const buf = ctx.createBuffer(1, 1, 22050);
      const src = ctx.createBufferSource();
      src.buffer = buf;
      src.connect(ctx.destination);
      src.start(0);
      await ctx.close();
    }
    if (window.speechSynthesis) {
      window.speechSynthesis.cancel();
      const u = new SpeechSynthesisUtterance(' ');
      u.volume = 0;
      window.speechSynthesis.speak(u);
      window.speechSynthesis.cancel();
    }
    audioUnlocked = true;
    vaLog('audio_unlocked');
  } catch (e) {
    vaWarn('audio_unlock_fail', { error: e instanceof Error ? e.message : String(e) });
  }
}

export function bargeInStopAll(): void {
  aborted = true;
  queue = [];
  pumping = false;
  speaking = false;
  if (currentAudio) {
    currentAudio.pause();
    currentAudio.src = '';
    currentAudio = null;
  }
  if (typeof window !== 'undefined' && window.speechSynthesis) {
    window.speechSynthesis.cancel();
  }
  // Короткий mute после стопа, чтобы не поймать хвост эха
  setMicMuted(500);
  vaLog('barge_in_stop');
}

export function resetTtsAbortFlag(): void {
  aborted = false;
}

export function pullSentences(buffer: string): { sentences: string[]; rest: string } {
  const sentences: string[] = [];
  let rest = buffer;
  const re = /([^.!?\n]+[.!?…]+)(\s+|$)|([^\n]+)\n+/g;
  let lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(rest)) !== null) {
    const piece = (m[1] || m[3] || '').trim();
    if (piece) sentences.push(piece);
    lastIndex = m.index + m[0].length;
  }
  rest = rest.slice(lastIndex);
  if (rest.length > 90) {
    const cut = rest.lastIndexOf(' ', 80);
    if (cut > 30) {
      sentences.push(rest.slice(0, cut).trim());
      rest = rest.slice(cut).trimStart();
    }
  }
  return { sentences, rest };
}

function resolveEngine(opts?: TtsVoiceOpts): 'silero' | 'browser' | 'omnivoice' {
  const settings = loadVoiceAssistantSettings();
  const pref = opts?.engine || settings.ttsEngine || 'auto';
  if (pref === 'browser') return 'browser';
  if (pref === 'omnivoice') return 'omnivoice';
  if (pref === 'silero') return 'silero';
  if (opts?.sileroAvailable === false) return 'browser';
  if (pref === 'auto') return 'browser';
  return 'silero';
}

function optsRate(v: number): number {
  return Math.min(2, Math.max(0.5, v || 1.05));
}

function pickRussianVoice(): SpeechSynthesisVoice | null {
  if (!window.speechSynthesis) return null;
  const voices = window.speechSynthesis.getVoices();
  return (
    voices.find((v) => /ru(-|_)?RU/i.test(v.lang) && /google/i.test(v.name)) ||
    voices.find((v) => /^ru/i.test(v.lang)) ||
    null
  );
}

async function speakBrowser(text: string): Promise<void> {
  if (aborted || !text.trim()) return;
  if (!window.speechSynthesis) {
    vaWarn('tts_no_speech_synthesis');
    return;
  }
  // Chrome: voices могут подгрузиться асинхронно
  if (window.speechSynthesis.getVoices().length === 0) {
    await new Promise<void>((resolve) => {
      const done = () => {
        window.speechSynthesis.onvoiceschanged = null;
        resolve();
      };
      window.speechSynthesis.onvoiceschanged = done;
      setTimeout(done, 300);
    });
  }

  setMicMuted(Math.max(1500, text.length * 60));
  await new Promise<void>((resolve) => {
    const utter = new SpeechSynthesisUtterance(text);
    utter.lang = 'ru-RU';
    const voice = pickRussianVoice();
    if (voice) utter.voice = voice;
    const settings = loadVoiceAssistantSettings();
    utter.rate = optsRate(settings.speechRate);
    utter.volume = 1;
    speaking = true;
    vaLog('tts_browser_speak', { text: text.slice(0, 120), voice: voice?.name || null });
    utter.onend = () => {
      speaking = false;
      setMicMuted(400);
      resolve();
    };
    utter.onerror = (ev) => {
      speaking = false;
      vaWarn('tts_browser_error', { error: String((ev as SpeechSynthesisErrorEvent).error || 'error') });
      resolve();
    };
    window.speechSynthesis.cancel();
    window.speechSynthesis.speak(utter);
  });
}

async function speakOmnivoice(text: string): Promise<void> {
  if (aborted || !text.trim()) return;
  try {
    const form = new FormData();
    form.append('text', text);
    const resp = await authFetch(getApiUrl(API_ENDPOINTS.VOICE_ASSISTANT_OMNIVOICE_SYNTH), {
      method: 'POST',
      body: form,
    });
    if (!resp.ok) {
      vaWarn('tts_omnivoice_fail', { status: resp.status });
      await speakBrowser(text);
      return;
    }
    const blob = await resp.blob();
    if (!blob.size) {
      await speakBrowser(text);
      return;
    }
    const url = URL.createObjectURL(blob);
    setMicMuted(Math.max(2000, text.length * 70));
    await new Promise<void>((resolve) => {
      const audio = new Audio(url);
      currentAudio = audio;
      speaking = true;
      vaLog('tts_omnivoice_play', { text: text.slice(0, 120) });
      audio.onended = () => {
        speaking = false;
        currentAudio = null;
        URL.revokeObjectURL(url);
        setMicMuted(400);
        resolve();
      };
      audio.onerror = () => {
        speaking = false;
        currentAudio = null;
        URL.revokeObjectURL(url);
        resolve();
      };
      void audio.play().catch(async () => {
        speaking = false;
        currentAudio = null;
        URL.revokeObjectURL(url);
        await speakBrowser(text);
        resolve();
      });
    });
  } catch (e) {
    vaWarn('tts_omnivoice_error', { error: e instanceof Error ? e.message : String(e) });
    await speakBrowser(text);
  }
}

async function speakSilero(text: string, opts?: TtsVoiceOpts): Promise<void> {
  if (aborted || !text.trim()) return;
  const settings = loadVoiceAssistantSettings();
  const voice_id = opts?.voice_id || settings.voiceId || 'ru';
  const voice_speaker = opts?.voice_speaker || settings.voiceSpeaker || 'baya';
  const speech_rate = optsRate(opts?.speech_rate ?? settings.speechRate);
  const cacheKey = `${voice_speaker}|${speech_rate}|${text}`;

  try {
    let url = audioCache.get(cacheKey);
    if (!url) {
      const resp = await authFetch(getApiUrl(API_ENDPOINTS.VOICE_SYNTHESIZE), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, voice_id, voice_speaker, speech_rate }),
      });
      if (!resp.ok) {
        vaWarn('tts_silero_fail', { status: resp.status });
        await speakBrowser(text);
        return;
      }
      const blob = await resp.blob();
      if (!blob.size) {
        await speakBrowser(text);
        return;
      }
      url = URL.createObjectURL(blob);
      if (audioCache.size > 40) {
        const first = audioCache.keys().next().value;
        if (first) {
          URL.revokeObjectURL(audioCache.get(first)!);
          audioCache.delete(first);
        }
      }
      audioCache.set(cacheKey, url);
    }
    if (aborted) return;

    setMicMuted(Math.max(2000, text.length * 70));
    await new Promise<void>((resolve) => {
      const audio = new Audio(url);
      currentAudio = audio;
      speaking = true;
      vaLog('tts_silero_play', { text: text.slice(0, 120) });
      audio.onended = () => {
        speaking = false;
        currentAudio = null;
        setMicMuted(400);
        resolve();
      };
      audio.onerror = () => {
        speaking = false;
        currentAudio = null;
        resolve();
      };
      void audio.play().catch(async (err) => {
        vaWarn('tts_autoplay_blocked', { error: String(err) });
        speaking = false;
        currentAudio = null;
        await speakBrowser(text);
        resolve();
      });
    });
  } catch (e) {
    vaWarn('tts_silero_error', { error: e instanceof Error ? e.message : String(e) });
    await speakBrowser(text);
  }
}

async function pump(opts?: TtsVoiceOpts): Promise<void> {
  if (pumping) return;
  pumping = true;
  const engine = resolveEngine(opts);
  try {
    while (queue.length && !aborted) {
      const item = queue.shift()!;
      vaLog('tts_chunk', { engine, text: item.text.slice(0, 120) });
      if (engine === 'omnivoice') await speakOmnivoice(item.text);
      else if (engine === 'silero') await speakSilero(item.text, opts);
      else await speakBrowser(item.text);
      item.resolve();
    }
  } finally {
    pumping = false;
    speaking = false;
    micMuteListeners.forEach((cb) => cb(isMicMutedForTts()));
  }
}

export function enqueueSpeak(text: string, opts?: TtsVoiceOpts): Promise<void> {
  const phrase = String(text || '').trim();
  if (!phrase || aborted) return Promise.resolve();
  return new Promise<void>((resolve) => {
    queue.push({ text: phrase, resolve });
    void pump(opts);
  });
}

export async function speakNow(text: string, opts?: TtsVoiceOpts): Promise<void> {
  queue = [];
  if (currentAudio) {
    currentAudio.pause();
    currentAudio.src = '';
    currentAudio = null;
  }
  if (typeof window !== 'undefined' && window.speechSynthesis) {
    window.speechSynthesis.cancel();
  }
  speaking = false;
  pumping = false;
  aborted = false;
  await enqueueSpeak(text, opts);
  await flushTtsQueue();
}

export async function flushTtsQueue(): Promise<void> {
  while ((pumping || queue.length > 0) && !aborted) {
    await new Promise((r) => setTimeout(r, 40));
  }
}
