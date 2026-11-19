import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAppActions, useAppContext } from '../contexts/AppContext';
import { useAuth } from '../contexts/AuthContext';
import { useSocket } from '../contexts/SocketContext';
import { executeVoiceIntent } from './intentExecutor';
import { matchLocalIntent } from './intentMatcher';
import { parseIntentRemote } from './parseRemote';
import { startLiveAsk, type LiveAskHandle } from './liveAsk';
import {
  loadVoiceAssistantSettings,
  updateVoiceAssistantSettings,
} from './settings';
import { ContinuousSpeechRecognition, isWebSpeechAvailable } from './speechRecognition';
import { speakConfirmation, stopVoiceAssistantSpeech } from './ttsConfirm';
import {
  bargeInStopAll,
  isLiveTtsSpeaking,
  isMicMutedForTts,
  unlockAudioPlayback,
} from './ttsQueue';
import { appendVaHistory } from './conversationHistory';
import { fetchVaTtsStatus } from './ttsStatus';
import { detectWakeWord } from './wakeWord';
import { WhisperFallbackListener } from './whisperFallback';
import VoiceAssistantStatusChip from './VoiceAssistantStatusChip';
import { vaLog, vaWarn } from './log';
import {
  ASTRA_ACTIVATE_VOICE_ASSISTANT,
  ASTRA_DEACTIVATE_VOICE_ASSISTANT,
} from '../constants/hotkeys';
import {
  VOICE_ASSISTANT_SETTINGS_CHANGED,
  type VoiceAssistantSettings,
  type VoiceAssistantStatus,
} from './types';

/** Пауза после конца фразы перед запуском команды (меньше = быстрее). */
const COMMAND_IDLE_MS = 550;
const COMMAND_MAX_MS = 6000;
const COMMAND_FINAL_MS = 120;
/** Игнор эха TTS сразу после старта озвучки */
const BARGE_IN_GRACE_MS = 900;

/**
 * Always-on VA: Web Speech STT + streaming LLM (SSE) + TTS по фразам + barge-in.
 */
export default function VoiceAssistantController() {
  const navigate = useNavigate();
  const { state } = useAppContext();
  const { createChat, setCurrentChat, deleteChat, showNotification } = useAppActions();
  const { sendMessage, stopGeneration } = useSocket();
  const { token } = useAuth();

  const [settings, setSettings] = useState<VoiceAssistantSettings>(() =>
    loadVoiceAssistantSettings(),
  );
  const [status, setStatus] = useState<VoiceAssistantStatus>({
    phase: 'idle',
    engine: 'none',
    micMuted: false,
  });

  const settingsRef = useRef(settings);
  const statusRef = useRef(status);
  const stateRef = useRef(state);
  const depsRef = useRef({
    navigate,
    createChat,
    setCurrentChat,
    deleteChat,
    sendMessage,
    stopGeneration,
    showNotification,
    token,
  });
  const commandBufferRef = useRef('');
  const commandTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const commandDeadlineRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const busyRef = useRef(false);
  const srRef = useRef<ContinuousSpeechRecognition | null>(null);
  const whisperRef = useRef<WhisperFallbackListener | null>(null);
  const runCommandRef = useRef<(raw: string) => Promise<void>>(async () => undefined);
  const beginCaptureRef = useRef<(seed?: string) => void>(() => undefined);
  const liveAskRef = useRef<LiveAskHandle | null>(null);
  const sileroOkRef = useRef<boolean | null>(null);
  const speakStartedAtRef = useRef(0);
  /** После включения VA горячей клавишей — сразу начать захват команды. */
  const pendingHotkeyActivateRef = useRef(false);
  /** Пауза микрофона без выключения ассистента. */
  const micMutedRef = useRef(false);

  settingsRef.current = settings;
  statusRef.current = status;
  stateRef.current = state;
  depsRef.current = {
    navigate,
    createChat,
    setCurrentChat,
    deleteChat,
    sendMessage,
    stopGeneration,
    showNotification,
    token,
  };

  const publishStatus = useCallback((patch: Partial<VoiceAssistantStatus>) => {
    setStatus((prev) => {
      const next = { ...prev, ...patch };
      statusRef.current = next;
      return next;
    });
  }, []);

  const clearCommandTimers = useCallback(() => {
    if (commandTimerRef.current) {
      clearTimeout(commandTimerRef.current);
      commandTimerRef.current = null;
    }
    if (commandDeadlineRef.current) {
      clearTimeout(commandDeadlineRef.current);
      commandDeadlineRef.current = null;
    }
  }, []);

  const doBargeIn = useCallback(
    (heard: string) => {
      vaLog('barge_in', { heard });
      liveAskRef.current?.abort();
      liveAskRef.current = null;
      bargeInStopAll();
      stopVoiceAssistantSpeech();
      busyRef.current = false;
      publishStatus({ phase: 'capturing_command', lastHeard: heard, lastError: undefined });
      // Остаток речи пользователя — новая команда/вопрос
      const cleaned = heard.trim();
      if (cleaned.length > 2) {
        commandBufferRef.current = cleaned;
        if (commandTimerRef.current) clearTimeout(commandTimerRef.current);
        commandTimerRef.current = setTimeout(() => {
          const buf = commandBufferRef.current.trim();
          if (buf) void runCommandRef.current(buf);
        }, 700);
      }
    },
    [publishStatus],
  );

  const runCommand = useCallback(
    async (raw: string) => {
      const text = String(raw || '').trim();
      if (!text || busyRef.current) return;
      busyRef.current = true;
      clearCommandTimers();
      commandBufferRef.current = '';
      publishStatus({ phase: 'executing', lastCommand: text, lastError: undefined, lastAnswer: undefined });
      vaLog('command', { text });
      appendVaHistory('user', text, 'question');

      const d = depsRef.current;
      const s = settingsRef.current;
      try {
        // Локальный match — без сети (быстрые UI-команды)
        let intent = matchLocalIntent(text);
        let source: 'local' | 'remote' | 'ask' | null = intent ? 'local' : null;
        // Remote parse только если локально не нашли (иначе +1–3с задержки)
        if (!intent) {
          const remote = await parseIntentRemote(text);
          if (remote && remote.id !== 'unknown') {
            intent = remote;
            source = 'remote';
          }
        }

        // Свободный вопрос → streaming LLM + TTS по фразам
        if (!intent || intent.id === 'unknown') {
          vaLog('fallback_ask_live', { text });
          publishStatus({ phase: 'speaking', lastHeard: text });
          speakStartedAtRef.current = Date.now();

          if (sileroOkRef.current === null) {
            const st = await fetchVaTtsStatus();
            sileroOkRef.current = st.available;
            publishStatus({
              sileroBlocked: st.blocked,
              ttsEngine: s.ttsEngine === 'silero' && st.available ? 'silero' : 'browser',
            });
          }

          const handle = startLiveAsk(
            text,
            {
              voice_id: s.voiceId,
              voice_speaker: s.voiceSpeaker,
              speech_rate: s.speechRate,
              // auto → browser (см. ttsQueue) — чтобы ответ было слышно
              engine: s.ttsEngine,
              sileroAvailable: sileroOkRef.current !== false,
            },
            {
              onToken: (_chunk, full) => {
                publishStatus({ lastAnswer: full, phase: 'speaking' });
                speakStartedAtRef.current = speakStartedAtRef.current || Date.now();
              },
              onDone: (full) => {
                publishStatus({ lastAnswer: full });
                if (full.trim()) appendVaHistory('assistant', full, 'answer');
              },
              onError: (err) => {
                publishStatus({ lastError: err });
                appendVaHistory('system', err, 'error');
              },
            },
          );
          liveAskRef.current = handle;
          const answer = await handle.done;
          liveAskRef.current = null;
          if (answer) {
            vaLog('answer', { text, answer: answer.slice(0, 200) });
            publishStatus({ lastCommand: text, lastAnswer: answer });
          } else {
            vaWarn('intent_unknown_or_empty', { text });
            if (!isLiveTtsSpeaking()) {
              const msg = 'Не поняла. Повторите вопрос.';
              appendVaHistory('assistant', msg, 'confirm');
              await speakConfirmation(msg);
            }
          }
          return;
        }

        vaLog('intent', {
          text,
          source,
          id: intent.id,
          params: intent.params || {},
          confidence: intent.confidence,
        });

        publishStatus({ phase: 'speaking' });
        speakStartedAtRef.current = Date.now();
        const result = await executeVoiceIntent(intent, {
          navigate: d.navigate,
          state: stateRef.current,
          createChat: d.createChat,
          setCurrentChat: d.setCurrentChat,
          deleteChat: d.deleteChat,
          sendMessage: d.sendMessage,
          stopGeneration: d.stopGeneration,
          showNotification: d.showNotification,
          token: d.token,
          confirmTts: s.confirmTts,
        });

        if (result.ok) {
          vaLog('action_ok', {
            text,
            intent: intent.id,
            confirm: result.confirmPhrase || null,
          });
          if (result.confirmPhrase) {
            appendVaHistory('assistant', result.confirmPhrase, 'confirm');
            publishStatus({ lastAnswer: result.confirmPhrase });
          }
        } else {
          vaWarn('action_fail', {
            text,
            intent: intent.id,
            error: result.error || null,
          });
          if (result.confirmPhrase && s.confirmTts) {
            appendVaHistory('assistant', result.confirmPhrase, 'confirm');
            await speakConfirmation(result.confirmPhrase);
          }
          if (result.error) {
            publishStatus({ lastError: result.error });
            appendVaHistory('system', result.error, 'error');
          }
        }
      } finally {
        busyRef.current = false;
        liveAskRef.current = null;
        if (settingsRef.current.enabled) {
          publishStatus({ phase: 'listening_wake' });
        } else {
          publishStatus({ phase: 'idle' });
        }
      }
    },
    [clearCommandTimers, publishStatus],
  );

  const beginCapture = useCallback(
    (seed = '') => {
      if (busyRef.current && !isLiveTtsSpeaking()) return;
      commandBufferRef.current = seed;
      vaLog('wake', { remainder: seed || null });
      publishStatus({ phase: 'capturing_command', lastHeard: seed || '…' });
      clearCommandTimers();
      commandDeadlineRef.current = setTimeout(() => {
        const buf = commandBufferRef.current.trim();
        if (buf) void runCommandRef.current(buf);
        else {
          vaLog('capture_timeout_empty');
          publishStatus({ phase: 'listening_wake' });
        }
      }, COMMAND_MAX_MS);
      if (seed.trim()) {
        commandTimerRef.current = setTimeout(() => {
          const buf = commandBufferRef.current.trim();
          if (buf) void runCommandRef.current(buf);
        }, COMMAND_IDLE_MS);
      }
    },
    [clearCommandTimers, publishStatus],
  );

  runCommandRef.current = runCommand;
  beginCaptureRef.current = beginCapture;

  const pauseMicListening = useCallback(() => {
    micMutedRef.current = true;
    clearCommandTimers();
    commandBufferRef.current = '';
    srRef.current?.stop();
    whisperRef.current?.stop();
    if (statusRef.current.phase === 'capturing_command') {
      busyRef.current = false;
    }
    publishStatus({
      micMuted: true,
      phase:
        statusRef.current.phase === 'executing' || statusRef.current.phase === 'speaking'
          ? statusRef.current.phase
          : 'listening_wake',
      lastHeard: undefined,
    });
    vaLog('mic_paused');
  }, [clearCommandTimers, publishStatus]);

  const resumeMicListening = useCallback(async () => {
    if (!settingsRef.current.enabled) return;
    micMutedRef.current = false;
    publishStatus({ micMuted: false, phase: 'listening_wake', lastError: undefined });
    vaLog('mic_resumed');
    if (srRef.current) {
      srRef.current.start();
      return;
    }
    if (whisperRef.current) {
      await whisperRef.current.start();
      return;
    }
    // Движки могли быть сброшены — пересоздаём как при старте
    if (isWebSpeechAvailable()) {
      const sr = new ContinuousSpeechRecognition(settingsRef.current.language, {
        onPartial: (t) => onSpeechTextRef.current(t, false),
        onFinal: (t) => onSpeechTextRef.current(t, true),
        onError: (err) => {
          vaWarn('sr_error', { error: err });
          publishStatus({ lastError: err });
        },
      });
      srRef.current = sr;
      sr.start();
      publishStatus({ engine: 'webspeech' });
      return;
    }
    const whisper = new WhisperFallbackListener(
      (t) => onSpeechTextRef.current(t, true),
      (err) => {
        vaWarn('whisper_error', { error: err });
        publishStatus({ lastError: err });
      },
    );
    whisperRef.current = whisper;
    const ok = await whisper.start();
    if (ok) publishStatus({ engine: 'whisper' });
  }, [publishStatus]);

  const toggleMicListening = useCallback(() => {
    if (!settingsRef.current.enabled) return;
    if (micMutedRef.current) {
      void resumeMicListening();
      depsRef.current.showNotification('info', 'Микрофон снова слушает');
    } else {
      pauseMicListening();
      depsRef.current.showNotification('info', 'Микрофон на паузе');
    }
  }, [pauseMicListening, resumeMicListening]);

  const onSpeechText = useCallback(
    (text: string, isFinal: boolean) => {
      if (!settingsRef.current.enabled) return;
      if (micMutedRef.current) return;

      const phase = statusRef.current.phase;
      publishStatus({ lastHeard: text });

      // Пока TTS играет — глушим mic (иначе эхо → barge-in → тишина)
      if (isMicMutedForTts()) {
        vaLog('heard_while_tts_muted', { text: text.slice(0, 80), isFinal });
        return;
      }

      // Barge-in только после grace и если mic не muted; нужен явный final
      if (
        (phase === 'speaking' || phase === 'executing' || isLiveTtsSpeaking() || liveAskRef.current) &&
        isFinal &&
        text.trim().length >= 5
      ) {
        const sinceSpeak = Date.now() - (speakStartedAtRef.current || 0);
        if (sinceSpeak > BARGE_IN_GRACE_MS) {
          doBargeIn(text);
          return;
        }
        vaLog('heard_during_grace', { text, isFinal });
        return;
      }

      if (busyRef.current && phase !== 'capturing_command') return;

      vaLog(isFinal ? 'heard_final' : 'heard', { text, phase });

      if (phase === 'listening_wake' || phase === 'idle') {
        const hit = detectWakeWord(text, settingsRef.current.wakeWord);
        if (!hit.matched) {
          if (isFinal) {
            vaLog('waiting_wake', {
              text,
              wakeWord: settingsRef.current.wakeWord,
            });
          }
          return;
        }
        if (hit.remainder) {
          beginCaptureRef.current(hit.remainder);
          if (isFinal) void runCommandRef.current(hit.remainder);
          return;
        }
        beginCaptureRef.current('');
        return;
      }

      if (phase === 'capturing_command') {
        const hit = detectWakeWord(text, settingsRef.current.wakeWord);
        const piece = hit.matched ? hit.remainder : text;
        if (!piece.trim()) return;
        commandBufferRef.current = piece.trim();
        publishStatus({ lastHeard: commandBufferRef.current });
        vaLog('capture_buffer', { text: commandBufferRef.current, isFinal });
        if (commandTimerRef.current) clearTimeout(commandTimerRef.current);
        commandTimerRef.current = setTimeout(() => {
          const buf = commandBufferRef.current.trim();
          if (buf) void runCommandRef.current(buf);
        }, isFinal ? COMMAND_FINAL_MS : COMMAND_IDLE_MS);
      }
    },
    [doBargeIn, publishStatus],
  );

  const onSpeechTextRef = useRef(onSpeechText);
  onSpeechTextRef.current = onSpeechText;

  const stopEngines = useCallback(() => {
    liveAskRef.current?.abort();
    liveAskRef.current = null;
    srRef.current?.abort();
    srRef.current = null;
    whisperRef.current?.stop();
    whisperRef.current = null;
    clearCommandTimers();
    stopVoiceAssistantSpeech();
    busyRef.current = false;
  }, [clearCommandTimers]);

  useEffect(() => {
    const onChange = () => setSettings(loadVoiceAssistantSettings());
    window.addEventListener(VOICE_ASSISTANT_SETTINGS_CHANGED, onChange);
    return () => window.removeEventListener(VOICE_ASSISTANT_SETTINGS_CHANGED, onChange);
  }, []);

  useEffect(() => {
    let cancelled = false;

    const start = async () => {
      stopEngines();
      micMutedRef.current = false;
      if (!settings.enabled) {
        publishStatus({ phase: 'idle', engine: 'none', micMuted: false });
        vaLog('disabled');
        return;
      }

      await unlockAudioPlayback();
      if (cancelled) return;

      const tts = await fetchVaTtsStatus();
      if (cancelled) return;
      sileroOkRef.current = tts.available;
      const effectiveTts =
        settings.ttsEngine === 'omnivoice'
          ? 'omnivoice'
          : settings.ttsEngine === 'silero' && tts.available
            ? 'silero'
            : settings.ttsEngine === 'browser' || settings.ttsEngine === 'auto'
              ? 'browser'
              : tts.available
                ? 'silero'
                : 'browser';
      publishStatus({
        sileroBlocked: tts.blocked,
        ttsEngine: effectiveTts as VoiceAssistantStatus['ttsEngine'],
      });
      if (tts.blocked && settings.ttsEngine === 'silero') {
        depsRef.current.showNotification(
          'warning',
          tts.hint || 'Silero TTS недоступен — используется голос браузера',
        );
      }
      if (settings.ttsEngine === 'omnivoice' && !tts.omnivoice?.available) {
        depsRef.current.showNotification(
          'warning',
          tts.omnivoice?.hint ||
            'OmniVoice недоступен. Запустите: docker compose up -d omnivoice-service',
        );
      }

      vaLog('starting', {
        wakeWord: settings.wakeWord,
        language: settings.language,
        webSpeech: isWebSpeechAvailable(),
        silero: tts.available,
        ttsEngine: effectiveTts,
        voice: settings.voiceSpeaker,
      });
      appendVaHistory('system', `Помощник включён (TTS: ${effectiveTts})`, 'info');

      if (isWebSpeechAvailable()) {
        const sr = new ContinuousSpeechRecognition(settings.language, {
          onPartial: (t) => onSpeechTextRef.current(t, false),
          onFinal: (t) => onSpeechTextRef.current(t, true),
          onError: (err) => {
            vaWarn('sr_error', { error: err });
            if (err === 'not-allowed' || err === 'service-not-allowed') {
              depsRef.current.showNotification(
                'error',
                'Нет доступа к микрофону для голосового помощника',
              );
              updateVoiceAssistantSettings({ enabled: false });
              return;
            }
            publishStatus({ lastError: err });
          },
          onStart: () => vaLog('sr_started'),
          onEnd: () => vaLog('sr_ended_restart'),
        });
        if (cancelled) return;
        srRef.current = sr;
        if (sr.start()) {
          publishStatus({
            phase: 'listening_wake',
            engine: 'webspeech',
            lastError: undefined,
            micMuted: false,
          });
          vaLog('listening', { engine: 'webspeech', wakeWord: settings.wakeWord });
          if (pendingHotkeyActivateRef.current) {
            pendingHotkeyActivateRef.current = false;
            beginCaptureRef.current('');
            depsRef.current.showNotification('info', 'Слушаю команду…');
          } else {
            depsRef.current.showNotification(
              'info',
              `Голосовой помощник слушает. Скажите «${settings.wakeWord}»`,
            );
          }
          return;
        }
      }

      const whisper = new WhisperFallbackListener(
        (t) => onSpeechTextRef.current(t, true),
        (err) => {
          vaWarn('whisper_error', { error: err });
          publishStatus({ lastError: err });
          if (/микрофон|NotAllowed|Permission/i.test(err)) {
            depsRef.current.showNotification('error', 'Нет доступа к микрофону');
            updateVoiceAssistantSettings({ enabled: false });
          }
        },
      );
      if (cancelled) return;
      whisperRef.current = whisper;
      const ok = await whisper.start();
      if (cancelled) return;
      if (ok) {
        publishStatus({
          phase: 'listening_wake',
          engine: 'whisper',
          lastError: undefined,
          micMuted: false,
        });
        vaLog('listening', { engine: 'whisper', wakeWord: settings.wakeWord });
        if (pendingHotkeyActivateRef.current) {
          pendingHotkeyActivateRef.current = false;
          beginCaptureRef.current('');
          depsRef.current.showNotification('info', 'Слушаю команду…');
        }
      } else {
        pendingHotkeyActivateRef.current = false;
        publishStatus({ phase: 'idle', engine: 'none', micMuted: false });
        updateVoiceAssistantSettings({ enabled: false });
      }
    };

    void start();
    return () => {
      cancelled = true;
      stopEngines();
    };
  }, [settings.enabled, settings.language, settings.wakeWord, stopEngines, publishStatus]);

  useEffect(() => {
    const onActivate = () => {
      void unlockAudioPlayback();
      vaLog('hotkey_activate');
      if (!settingsRef.current.enabled) {
        pendingHotkeyActivateRef.current = true;
        micMutedRef.current = false;
        updateVoiceAssistantSettings({ enabled: true });
        return;
      }
      if (micMutedRef.current) {
        void resumeMicListening().then(() => {
          beginCaptureRef.current('');
          depsRef.current.showNotification('info', 'Слушаю команду…');
        });
        return;
      }
      if (statusRef.current.phase === 'capturing_command') {
        depsRef.current.showNotification('info', 'Уже слушаю команду');
        return;
      }
      if (busyRef.current && !isLiveTtsSpeaking() && !liveAskRef.current) return;
      if (isLiveTtsSpeaking() || liveAskRef.current) {
        bargeInStopAll();
        liveAskRef.current?.abort();
        liveAskRef.current = null;
        stopVoiceAssistantSpeech();
      }
      beginCaptureRef.current('');
      depsRef.current.showNotification('info', 'Слушаю команду…');
    };
    const onDeactivate = () => {
      vaLog('hotkey_deactivate');
      pendingHotkeyActivateRef.current = false;
      if (!settingsRef.current.enabled) {
        depsRef.current.showNotification('info', 'Голосовой помощник уже выключен');
        return;
      }
      liveAskRef.current?.abort();
      liveAskRef.current = null;
      bargeInStopAll();
      stopVoiceAssistantSpeech();
      clearCommandTimers();
      busyRef.current = false;
      updateVoiceAssistantSettings({ enabled: false });
      publishStatus({ phase: 'idle', lastError: undefined });
      depsRef.current.showNotification('info', 'Голосовой помощник выключен');
    };
    window.addEventListener(ASTRA_ACTIVATE_VOICE_ASSISTANT, onActivate);
    window.addEventListener(ASTRA_DEACTIVATE_VOICE_ASSISTANT, onDeactivate);
    return () => {
      window.removeEventListener(ASTRA_ACTIVATE_VOICE_ASSISTANT, onActivate);
      window.removeEventListener(ASTRA_DEACTIVATE_VOICE_ASSISTANT, onDeactivate);
    };
  }, [clearCommandTimers, publishStatus, resumeMicListening]);

  useEffect(() => {
    const onVis = () => {
      if (!settingsRef.current.enabled) return;
      if (document.hidden) {
        vaLog('tab_hidden_pause');
        liveAskRef.current?.abort();
        srRef.current?.stop();
        whisperRef.current?.stop();
      } else if (!micMutedRef.current) {
        vaLog('tab_visible_resume');
        if (srRef.current) srRef.current.start();
        else if (whisperRef.current) void whisperRef.current.start();
      }
    };
    document.addEventListener('visibilitychange', onVis);
    return () => document.removeEventListener('visibilitychange', onVis);
  }, []);

  return (
    <VoiceAssistantStatusChip
      status={status}
      enabled={settings.enabled}
      onToggleMic={toggleMicListening}
    />
  );
}
