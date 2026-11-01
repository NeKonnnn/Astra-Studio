import { extractReasoningBlock } from '@/utils/reasoningSplit';

describe('extractReasoningBlock: plain-text рассуждение, границы ответа', () => {
  const filler = 'анализ запроса '.repeat(20).trim();

  it('пропускает подпункты, Wait, bullet, внутренние секции и пустые строки, режет по реальному ответу', () => {
    const text = [
      'Thinking Process:',
      filler,
      '1. **Шаг первый**',
      'Wait, проверим ещё раз',
      '* пункт списка',
      '**Drafting** черновик',
      '- ещё пункт',
      '• точка',
      '',
      'Итоговый ответ пользователю',
    ].join('\n\n');
    const r = extractReasoningBlock(text, false);
    expect(r.visibleContent).toBe('Итоговый ответ пользователю');
    expect(r.reasoningContent).toContain('Thinking Process:');
    expect(r.reasoningContent).toContain('• точка');
    expect(r.isThinkingStreaming).toBe(false);
  });

  it('первые абзацы короче минимальной границы не считаются границей ответа', () => {
    const text = `Рассуждение модели:\nкоротко\n\nпохоже на ответ\n\n${filler}\n\nнастоящий ответ`;
    const r = extractReasoningBlock(text, false);
    expect(r.visibleContent).toBe('настоящий ответ');
    expect(r.reasoningContent).toContain('похоже на ответ');
  });

  it('без границы: при стриминге всё считается рассуждением, иначе остаётся видимым', () => {
    const text = `Chain of Thought:\n${filler} без границы`;
    const streaming = extractReasoningBlock(text, true);
    expect(streaming.visibleContent).toBe('');
    expect(streaming.isThinkingStreaming).toBe(true);
    expect(streaming.reasoningContent).toContain('Chain of Thought:');
    const done = extractReasoningBlock(text, false);
    expect(done.visibleContent).toBe(text);
    expect(done.reasoningContent).toBeNull();
  });

  it('если после последнего абзаца нет пустых строк-границ, ничего не выделяется', () => {
    const text = `Thinking Process:\n${filler}\n\n1. **Последний подпункт**`;
    const r = extractReasoningBlock(text, false);
    expect(r.visibleContent).toBe(text);
    expect(r.reasoningContent).toBeNull();
  });
});
