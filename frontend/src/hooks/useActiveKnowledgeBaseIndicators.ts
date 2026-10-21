import { useEffect, useState } from 'react';
import {
  getActiveKnowledgeBaseRefs,
  KB_SELECTION_CHANGED_EVENT,
  type ActiveKnowledgeBaseRef,
} from '../utils/knowledgeBaseSelectionStorage';

/** Активные базы знаний текущего чата из localStorage + событие выбора. */
export function useActiveKnowledgeBaseIndicators(
  chatId?: string | null,
): ActiveKnowledgeBaseRef[] {
  const [items, setItems] = useState<ActiveKnowledgeBaseRef[]>(() =>
    getActiveKnowledgeBaseRefs(chatId),
  );

  useEffect(() => {
    const sync = (e?: Event) => {
      const detailChatId = (e as CustomEvent<{ chatId?: string }> | undefined)?.detail?.chatId;
      if (detailChatId && chatId && detailChatId !== chatId) return;
      setItems(getActiveKnowledgeBaseRefs(chatId));
    };
    sync();
    window.addEventListener(KB_SELECTION_CHANGED_EVENT, sync as EventListener);
    window.addEventListener('storage', sync);
    return () => {
      window.removeEventListener(KB_SELECTION_CHANGED_EVENT, sync as EventListener);
      window.removeEventListener('storage', sync);
    };
  }, [chatId]);

  return items;
}
