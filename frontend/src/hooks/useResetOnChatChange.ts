import { useEffect, useRef } from 'react';

/**
 * Вызывает `onChatChange` только при РЕАЛЬНОЙ смене идентификатора чата.
 *
 * На монтировании (и при повторном запуске эффекта в StrictMode) колбэк не вызывается:
 * иначе сброс черновика затирал бы текст, отложенный в sessionStorage эффектом вставки (BUG-FE-21).
 */
export function useResetOnChatChange(chatId: string | null, onChatChange: () => void): void {
  const previousChatIdRef = useRef<string | null | undefined>(undefined);
  const onChatChangeRef = useRef(onChatChange);
  onChatChangeRef.current = onChatChange;

  useEffect(() => {
    const previous = previousChatIdRef.current;
    previousChatIdRef.current = chatId;
    if (previous === undefined || previous === chatId) return;
    onChatChangeRef.current();
  }, [chatId]);
}
