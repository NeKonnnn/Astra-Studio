/**
 * @jest-environment jsdom
 */
import {
  clearActiveKnowledgeBases,
  getActiveKnowledgeBaseIds,
  getActiveKnowledgeBaseRefs,
  setActiveKnowledgeBaseRefs,
  toggleActiveKnowledgeBase,
} from '@/utils/knowledgeBaseSelectionStorage';

describe('knowledgeBaseSelectionStorage (local)', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('stores knowledge bases per chatId', () => {
    expect(getActiveKnowledgeBaseIds('c1')).toEqual([]);
    setActiveKnowledgeBaseRefs('c1', [{ id: 7, name: 'Docs' }]);
    expect(getActiveKnowledgeBaseIds('c1')).toEqual([7]);
    expect(getActiveKnowledgeBaseRefs('c1')[0]).toEqual({ id: 7, name: 'Docs' });
    expect(getActiveKnowledgeBaseIds('c2')).toEqual([]);
  });

  it('toggles and clears selection', () => {
    toggleActiveKnowledgeBase('c1', 3, 'A');
    toggleActiveKnowledgeBase('c1', 5, 'B');
    expect(getActiveKnowledgeBaseIds('c1')).toEqual([3, 5]);
    toggleActiveKnowledgeBase('c1', 3);
    expect(getActiveKnowledgeBaseIds('c1')).toEqual([5]);
    clearActiveKnowledgeBases('c1');
    expect(getActiveKnowledgeBaseIds('c1')).toEqual([]);
  });
});
