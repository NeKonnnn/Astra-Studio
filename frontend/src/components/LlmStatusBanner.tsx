import React, { useEffect, useRef, useState } from 'react';
import { getApiUrl } from '../config/api';
import { getSettings, initSettings } from '../settings';
import { STATUS_TOAST_AUTO_DISMISS_MS } from '../constants/statusToast';
import StatusToast from './StatusToast';

/**
 * Красный StatusToast сверху, если бэкенд сообщает, что до LLM (llm-svc) достучаться нельзя.
 * URL сервиса моделей фронт не знает — только GET /api/llm/status.
 */
export default function LlmStatusBanner() {
  const [open, setOpen] = useState(false);
  const [message, setMessage] = useState('');
  const [dismissed, setDismissed] = useState(false);
  const lastPollTsRef = useRef<number | null>(null);

  useEffect(() => {
    let interval: ReturnType<typeof setInterval> | undefined;
    let cancelled = false;

    const startPolling = async () => {
      let settings;
      try {
        settings = getSettings();
      } catch {
        try {
          settings = await initSettings();
        } catch {
          return;
        }
      }
      if (cancelled) return;
      const pollIntervalSeconds = settings.app.llmStatusPollSeconds;
      const tracePollEnabled = settings.app.llmStatusPollTrace === true;

      const poll = async () => {
        if (tracePollEnabled) {
          const now = Date.now();
          const prev = lastPollTsRef.current;
          const deltaSec = prev ? ((now - prev) / 1000).toFixed(2) : 'first';
          lastPollTsRef.current = now;
          console.info(`[LLM_STATUS_POLL] /api/llm/status tick interval=${pollIntervalSeconds}s delta=${deltaSec}s`);
        }
        try {
          const res = await fetch(getApiUrl('/api/llm/status'));
          if (!res.ok) return;
          const data = await res.json();
          if (data.use_llm_svc === true && data.connected === false) {
            setOpen(!dismissed);
            setMessage(
              typeof data.message === 'string' && data.message.trim()
                ? data.message
                : 'Подключиться к LLM не удалось. Проверьте сервис моделей и конфигурацию на сервере.'
            );
          } else {
            setOpen(false);
            setDismissed(false);
            setMessage('');
          }
        } catch {
          setOpen(false);
        }
      };

      void poll();
      interval = setInterval(poll, pollIntervalSeconds * 1000);
    };

    void startPolling();
    return () => {
      cancelled = true;
      if (interval) clearInterval(interval);
    };
  }, [dismissed]);

  if (!open || !message) return null;

  return (
    <StatusToast
      type="error"
      message={message}
      onClose={() => {
        setOpen(false);
        setDismissed(true);
      }}
      ariaLabel="Закрыть уведомление о подключении к LLM"
      fixed
      top={12}
      zIndexOffset={2}
      autoDismissMs={STATUS_TOAST_AUTO_DISMISS_MS}
    />
  );
}
