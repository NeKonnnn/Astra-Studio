import { useEffect, useState } from 'react';

/**
 * После стрима — сразу финальный content.
 * На стриме setState не вызываем (preview = спиннер); возвращаем live content as-is.
 */
export function useCommittedContent(
  content: string,
  isStreaming: boolean,
  _delayMs = 450,
): string {
  const [committed, setCommitted] = useState(content);

  useEffect(() => {
    if (isStreaming) return;
    setCommitted((prev) => (prev === content ? prev : content));
  }, [content, isStreaming]);

  return isStreaming ? content : committed;
}
