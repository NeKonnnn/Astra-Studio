import type { Message } from '@/contexts/AppContext';
import { buildBranchChatTitle, cloneMessagesForBranch } from '@/utils/branchChat';

function msg(partial: Partial<Message>): Message {
  return { id: 'orig', role: 'user', content: 'text', ...partial } as Message;
}

describe('buildBranchChatTitle', () => {
  it('prefixes the title', () => {
    expect(buildBranchChatTitle('Планирование')).toBe('Ветка · Планирование');
  });

  it('uses a default for an empty title', () => {
    expect(buildBranchChatTitle('   ')).toBe('Ветка · Чат');
  });

  it('truncates long titles to 40 characters', () => {
    expect(buildBranchChatTitle('я'.repeat(100))).toBe(`Ветка · ${'я'.repeat(40)}`);
  });
});

describe('cloneMessagesForBranch', () => {
  it('returns nothing for an out-of-range index', () => {
    const messages = [msg({})];

    expect(cloneMessagesForBranch(messages, -1)).toEqual([]);
    expect(cloneMessagesForBranch(messages, 1)).toEqual([]);
    expect(cloneMessagesForBranch([], 0)).toEqual([]);
  });

  it('copies messages up to the index inclusive with fresh ids', () => {
    const messages = [msg({ id: 'a' }), msg({ id: 'b', role: 'assistant' }), msg({ id: 'c' })];

    const cloned = cloneMessagesForBranch(messages, 1);

    expect(cloned).toHaveLength(2);
    expect(cloned.map((m) => m.id)).not.toContain('a');
    expect(cloned.every((m) => /^msg_[0-9a-f]{12}$/.test(m.id))).toBe(true);
    expect(new Set(cloned.map((m) => m.id)).size).toBe(2);
    expect(cloned.map((m) => m.content)).toEqual(['text', 'text']);
  });

  it('stops streaming flags', () => {
    const cloned = cloneMessagesForBranch(
      [msg({ isStreaming: true, multiLLMResponses: [{ content: 'x', isStreaming: true } as never] })],
      0,
    );

    expect(cloned[0].isStreaming).toBe(false);
    expect(cloned[0].multiLLMResponses?.[0].isStreaming).toBe(false);
  });

  it('does not share mutable arrays with the source', () => {
    const source = msg({ alternativeResponses: ['a', 'b'], mcpToolCalls: [{} as never] });

    const [cloned] = cloneMessagesForBranch([source], 0);
    cloned.alternativeResponses?.push('c');

    expect(source.alternativeResponses).toEqual(['a', 'b']);
    expect(cloned.mcpToolCalls).not.toBe(source.mcpToolCalls);
  });

  it('keeps the selected alternative of an assistant branch point', () => {
    const source = msg({
      role: 'assistant',
      content: 'основной',
      alternativeResponses: ['вариант 0', 'вариант 1'],
      currentResponseIndex: 1,
    });

    const [cloned] = cloneMessagesForBranch([source], 0);

    expect(cloned.content).toBe('вариант 1');
    expect(cloned.alternativeResponses).toBeUndefined();
    expect(cloned.currentResponseIndex).toBeUndefined();
  });

  it('falls back to the first alternative for an invalid index', () => {
    const source = msg({
      role: 'assistant',
      alternativeResponses: ['первый', 'второй'],
      currentResponseIndex: 9,
    });

    expect(cloneMessagesForBranch([source], 0)[0].content).toBe('первый');
  });

  it('picks the requested multi-LLM slot at the branch point', () => {
    const source = msg({
      role: 'assistant',
      content: 'общий',
      multiLLMResponses: [
        { content: 'модель А' } as never,
        { content: 'модель Б', alternativeResponses: ['б0', 'б1'], currentResponseIndex: 1 } as never,
      ],
    });

    expect(cloneMessagesForBranch([source], 0, 0)[0].content).toBe('модель А');
    const second = cloneMessagesForBranch([source], 0, 1)[0];
    expect(second.content).toBe('б1');
    expect(second.multiLLMResponses).toBeUndefined();
  });

  it('keeps non-branch-point assistant messages as they were', () => {
    const source = msg({ role: 'assistant', alternativeResponses: ['a', 'b'], currentResponseIndex: 1 });

    const [cloned] = cloneMessagesForBranch([source, msg({})], 1);

    expect(cloned.alternativeResponses).toEqual(['a', 'b']);
    expect(cloned.currentResponseIndex).toBe(1);
  });

  it('does not alter user messages at the branch point', () => {
    const [cloned] = cloneMessagesForBranch([msg({ content: 'вопрос' })], 0);

    expect(cloned.content).toBe('вопрос');
  });
});
