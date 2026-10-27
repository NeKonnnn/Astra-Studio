/**
 * @jest-environment jsdom
 */
import {
  extractReasoningBlock,
  stripReasoningMarkers,
} from '@/utils/reasoningSplit';

describe('extractReasoningBlock', () => {
  it('marks unclosed <think> as streaming and exposes reasoning immediately', () => {
    const parsed = extractReasoningBlock('<think>Шаг 1: смотрю документы', true);
    expect(parsed.isThinkingStreaming).toBe(true);
    expect(parsed.reasoningContent).toContain('Шаг 1');
    expect(parsed.visibleContent).toBe('');
  });

  it('shows empty reasoning shell for bare open <think> while streaming', () => {
    const parsed = extractReasoningBlock('<think>', true);
    expect(parsed.isThinkingStreaming).toBe(true);
    expect(parsed.reasoningContent).toBe('');
    expect(parsed.visibleContent).toBe('');
  });

  it('closes thinking when </think> and answer follow', () => {
    const parsed = extractReasoningBlock(
      '<think>внутренняя мысль</think>\n\nИтоговый ответ',
      true,
    );
    expect(parsed.isThinkingStreaming).toBe(false);
    expect(parsed.reasoningContent).toBe('внутренняя мысль');
    expect(parsed.visibleContent).toContain('Итоговый ответ');
  });

  it('stripReasoningMarkers removes closed and open think blocks', () => {
    expect(stripReasoningMarkers('<think>x</think>\n\nok')).toBe('ok');
    expect(stripReasoningMarkers('<think>partial')).toBe('');
  });
});
