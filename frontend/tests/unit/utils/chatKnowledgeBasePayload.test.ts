/**
 * @jest-environment jsdom
 *
 * Контракт: выбранные в «Инструменты → Базы знаний» id уходят в chat_message
 * как knowledge_base_ids (бэкенд handlers.chat_message).
 */
import {
  clearActiveKnowledgeBases,
  getActiveKnowledgeBaseIds,
  setActiveKnowledgeBaseRefs,
} from '@/utils/knowledgeBaseSelectionStorage';

function buildChatMessageKnowledgeBaseIds(chatId: string): number[] | undefined {
  const ids = getActiveKnowledgeBaseIds(chatId);
  return ids.length ? ids : undefined;
}

describe('chat_message knowledge_base_ids payload', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('omits field when no KB selected', () => {
    expect(buildChatMessageKnowledgeBaseIds('chat-kb-payload')).toBeUndefined();
  });

  it('includes selected Tools KB ids', () => {
    setActiveKnowledgeBaseRefs('chat-kb-payload', [
      { id: 12, name: 'Docs' },
      { id: 34, name: 'Policy' },
    ]);
    expect(buildChatMessageKnowledgeBaseIds('chat-kb-payload')).toEqual([12, 34]);
  });
});
