/**
 * Streaming LLM ask (SSE) + озвучка по предложениям + поддержка Abort/barge-in.
 */

import { getApiUrl, API_ENDPOINTS, getAuthFetchHeaders } from '../config/api';
import { vaLog, vaWarn } from './log';
import {
  bargeInStopAll,
  enqueueSpeak,
  flushTtsQueue,
  pullSentences,
  resetTtsAbortFlag,
  type TtsVoiceOpts,
} from './ttsQueue';

export type LiveAskHandlers = {
  onToken?: (chunk: string, full: string) => void;
  onDone?: (full: string) => void;
  onError?: (error: string) => void;
};

export type LiveAskHandle = {
  abort: () => void;
  done: Promise<string>;
};

/** Запустить streaming ask; токены озвучиваются по предложениям. */
export function startLiveAsk(
  text: string,
  ttsOpts?: TtsVoiceOpts,
  handlers?: LiveAskHandlers,
): LiveAskHandle {
  const controller = new AbortController();
  let full = '';
  let sentenceBuf = '';
  let aborted = false;

  const abort = () => {
    aborted = true;
    controller.abort();
    bargeInStopAll();
    vaLog('live_ask_abort');
  };

  const done = (async () => {
    resetTtsAbortFlag();
    bargeInStopAll();
    resetTtsAbortFlag();
    vaLog('live_ask_start', { text });

    try {
      const headers = {
        ...getAuthFetchHeaders(),
        'Content-Type': 'application/json',
        Accept: 'text/event-stream',
      };
      const resp = await fetch(getApiUrl(API_ENDPOINTS.VOICE_ASSISTANT_ASK_STREAM), {
        method: 'POST',
        headers,
        body: JSON.stringify({ text }),
        signal: controller.signal,
        credentials: 'include',
      });
      if (!resp.ok || !resp.body) {
        vaWarn('live_ask_http', { status: resp.status });
        handlers?.onError?.(`HTTP ${resp.status}`);
        return '';
      }

      const reader = resp.body.getReader();
      const decoder = new TextDecoder('utf-8');
      let carry = '';

      while (!aborted) {
        const { value, done: streamDone } = await reader.read();
        if (streamDone) break;
        carry += decoder.decode(value, { stream: true });
        const parts = carry.split('\n\n');
        carry = parts.pop() || '';
        for (const part of parts) {
          const line = part
            .split('\n')
            .map((l) => l.trim())
            .find((l) => l.startsWith('data:'));
          if (!line) continue;
          const raw = line.slice(5).trim();
          if (!raw) continue;
          let evt: { type?: string; chunk?: string; text?: string; error?: string };
          try {
            evt = JSON.parse(raw);
          } catch {
            continue;
          }
          if (evt.type === 'token' && evt.chunk) {
            full = evt.text || full + evt.chunk;
            sentenceBuf += evt.chunk;
            handlers?.onToken?.(evt.chunk, full);
            const { sentences, rest } = pullSentences(sentenceBuf);
            sentenceBuf = rest;
            for (const s of sentences) {
              if (aborted) break;
              void enqueueSpeak(s, ttsOpts);
            }
          } else if (evt.type === 'error') {
            handlers?.onError?.(evt.error || 'error');
            vaWarn('live_ask_error', { error: evt.error });
          } else if (evt.type === 'done') {
            // хвост
            const tail = sentenceBuf.trim();
            sentenceBuf = '';
            if (tail && !aborted) await enqueueSpeak(tail, ttsOpts);
            await flushTtsQueue();
            handlers?.onDone?.(full);
            vaLog('live_ask_done', { answer: full.slice(0, 200) });
          }
        }
      }

      // если поток оборвался без done
      const tail = sentenceBuf.trim();
      if (tail && !aborted) {
        await enqueueSpeak(tail, ttsOpts);
        await flushTtsQueue();
      }
      return full.trim();
    } catch (e) {
      if ((e as Error)?.name === 'AbortError') {
        vaLog('live_ask_aborted');
        return full.trim();
      }
      vaWarn('live_ask_exception', { error: e instanceof Error ? e.message : String(e) });
      handlers?.onError?.(e instanceof Error ? e.message : String(e));
      return full.trim();
    }
  })();

  return { abort, done };
}
