import { useCallback, useRef, useState } from 'react';
import type { InlineAttachment } from '../components/ChatInputBar';

export type QueuedChatMessage = {
  id: string;
  text: string;
  inlineAttachments: InlineAttachment[];
  expectMultiLlm?: boolean;
};

function newQueueId(): string {
  return `q-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

/** Очередь сообщений на чат: набор во время ответа, автоотправка после завершения. */
export function useChatMessageQueue() {
  const queuesRef = useRef<Record<string, QueuedChatMessage[]>>({});
  const [queues, setQueues] = useState<Record<string, QueuedChatMessage[]>>({});
  const drainingRef = useRef<Record<string, boolean>>({});

  const sync = useCallback((next: Record<string, QueuedChatMessage[]>) => {
    queuesRef.current = next;
    setQueues(next);
  }, []);

  const enqueue = useCallback(
    (
      chatId: string,
      item: Omit<QueuedChatMessage, 'id'>,
    ): QueuedChatMessage => {
      const entry: QueuedChatMessage = { ...item, id: newQueueId() };
      const prev = queuesRef.current[chatId] || [];
      sync({ ...queuesRef.current, [chatId]: [...prev, entry] });
      return entry;
    },
    [sync],
  );

  const remove = useCallback(
    (chatId: string, itemId: string) => {
      const prev = queuesRef.current[chatId] || [];
      const nextList = prev.filter((x) => x.id !== itemId);
      if (nextList.length === prev.length) return;
      if (!nextList.length) {
        const { [chatId]: _removed, ...rest } = queuesRef.current;
        sync(rest);
        return;
      }
      sync({ ...queuesRef.current, [chatId]: nextList });
    },
    [sync],
  );

  const update = useCallback(
    (
      chatId: string,
      itemId: string,
      patch: Partial<Omit<QueuedChatMessage, 'id'>>,
    ) => {
      const prev = queuesRef.current[chatId] || [];
      let changed = false;
      const nextList = prev.map((item) => {
        if (item.id !== itemId) return item;
        changed = true;
        return { ...item, ...patch, id: item.id };
      });
      if (!changed) return;
      sync({ ...queuesRef.current, [chatId]: nextList });
    },
    [sync],
  );

  const clear = useCallback(
    (chatId: string) => {
      if (!queuesRef.current[chatId]?.length) return;
      const { [chatId]: _removed, ...rest } = queuesRef.current;
      sync(rest);
    },
    [sync],
  );

  const shift = useCallback(
    (chatId: string): QueuedChatMessage | undefined => {
      const prev = queuesRef.current[chatId] || [];
      if (!prev.length) return undefined;
      const [first, ...rest] = prev;
      if (!rest.length) {
        const { [chatId]: _removed, ...restMap } = queuesRef.current;
        sync(restMap);
      } else {
        sync({ ...queuesRef.current, [chatId]: rest });
      }
      return first;
    },
    [sync],
  );

  const peek = useCallback((chatId: string): QueuedChatMessage | undefined => {
    return queuesRef.current[chatId]?.[0];
  }, []);

  const getQueue = useCallback(
    (chatId: string | null | undefined): QueuedChatMessage[] => {
      if (!chatId) return [];
      return queues[chatId] || [];
    },
    [queues],
  );

  /** Актуальная очередь из ref (без ожидания re-render) — для антидубля. */
  const getQueueSync = useCallback((chatId: string | null | undefined): QueuedChatMessage[] => {
    if (!chatId) return [];
    return queuesRef.current[chatId] || [];
  }, []);

  const beginDrain = useCallback((chatId: string): boolean => {
    if (drainingRef.current[chatId]) return false;
    drainingRef.current[chatId] = true;
    return true;
  }, []);

  const endDrain = useCallback((chatId: string) => {
    drainingRef.current[chatId] = false;
  }, []);

  return {
    queues,
    enqueue,
    remove,
    update,
    clear,
    shift,
    peek,
    getQueue,
    getQueueSync,
    beginDrain,
    endDrain,
  };
}
