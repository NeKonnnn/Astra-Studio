import {
  analyzeConversationTokens,
  estimateTokens,
  resolveModelNativeContextSize,
} from '@/utils/contextTokens';

const msg = (over: Record<string, unknown>) =>
  ({ id: 'm', role: 'assistant', content: '', timestamp: new Date(), ...over } as any);

describe('contextTokens: недостающие ветки', () => {
  it('resolveModelNativeContextSize: нулевые строки в extra игнорируются, суффиксы k/m понимаются', () => {
    expect(resolveModelNativeContextSize({ path: 'p', extra: { context_size: '0' } } as any)).toBeNull();
    expect(resolveModelNativeContextSize({ path: 'p', extra: { n_ctx: '0k' } } as any)).toBeNull();
    expect(resolveModelNativeContextSize({ path: 'p', extra: { n_ctx: '8k' } } as any)).toBe(8000);
    expect(resolveModelNativeContextSize({ path: 'p', extra: { n_ctx: '1,5M' } } as any)).toBe(1500000);
    expect(resolveModelNativeContextSize({ path: 'p', extra: { n_ctx: '4096 ' } } as any)).toBe(4096);
    expect(resolveModelNativeContextSize({ path: 'p', extra: { n_ctx: 'abc' } } as any)).toBeNull();
  });

  it('analyzeConversationTokens: ответы мульти-LLM и альтернативы считаются в память после завершения', () => {
    const slots = [
      {
        content: 'основной',
        alternativeResponses: ['основной', 'альтернатива'],
        currentResponseIndex: 1,
      },
      { content: 'второй', alternativeResponses: ['второй'], currentResponseIndex: 0 },
    ];
    const done = analyzeConversationTokens([
      msg({ role: 'user', content: 'вопрос' }),
      msg({ content: 'общий', multiLLMResponses: slots, generationDurationSec: 2 }),
    ]);
    const expected =
      estimateTokens('вопрос') + 4 +
      estimateTokens('общий') + estimateTokens('основной') + estimateTokens('альтернатива') + estimateTokens('второй') + 4;
    expect(done.memoryTokens).toBe(expected);
    expect(done.generationTokens).toBe(0);
  });

  it('analyzeConversationTokens: стримящийся слот мульти-LLM держит ход открытым', () => {
    const r = analyzeConversationTokens([
      msg({ role: 'user', content: 'вопрос' }),
      msg({ content: 'частично', multiLLMResponses: [{ content: 'a', isStreaming: true }] }),
    ]);
    expect(r.generationTokens).toBe(estimateTokens('частично') + estimateTokens('a') + 4);
    expect(r.currentUserTokens).toBe(estimateTokens('вопрос') + 4);
    expect(r.memoryTokens).toBe(0);
  });

  it('analyzeConversationTokens: завершённый ответ с залипшим isStreaming не считается стримом', () => {
    const r = analyzeConversationTokens([
      msg({ role: 'user', content: 'q' }),
      msg({ content: 'a', isStreaming: true, generationDurationSec: 3 }),
    ]);
    expect(r.generationTokens).toBe(0);
    expect(r.memoryTokens).toBeGreaterThan(0);
  });

  it('analyzeConversationTokens: открытый ход, ответ раньше последнего user уходит в память', () => {
    const r = analyzeConversationTokens([
      msg({ role: 'user', content: 'старый вопрос' }),
      msg({ content: 'старый ответ' }),
      msg({ role: 'user', content: 'новый' }),
    ]);
    expect(r.memoryTokens).toBe(estimateTokens('старый вопрос') + 4 + estimateTokens('старый ответ') + 4);
    expect(r.currentUserTokens).toBe(estimateTokens('новый') + 4);
  });

  it('analyzeConversationTokens: только ассистентские сообщения (нет user) - считаются как генерация', () => {
    const r = analyzeConversationTokens([msg({ content: 'привет', isStreaming: true })]);
    // без user ход не «открыт», поэтому всё уходит в память
    expect(r.memoryTokens).toBe(estimateTokens('привет') + 4);
    expect(r.generationTokens).toBe(0);
  });
});
