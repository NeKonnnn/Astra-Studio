/**
 * @jest-environment node
 */
import {
  formatGenerationCompleteToast,
  generationCompleteToastTitle,
  previewGenerationReply,
  shouldShowGenerationCompleteToast,
  GENERATION_COMPLETE_TOAST_DISMISS_MS,
} from '@/utils/generationCompleteToast';

describe('generationCompleteToast', () => {
  it('strips thinking and collapses whitespace in preview', () => {
    const raw = '<think>secret</think>\n\nHello   world\nnext';
    expect(previewGenerationReply(raw)).toBe('Hello world next');
  });

  it('truncates long replies with ellipsis', () => {
    const raw = 'a'.repeat(200);
    const preview = previewGenerationReply(raw, 20);
    expect(preview.endsWith('…')).toBe(true);
    expect(preview.length).toBeLessThanOrEqual(20);
  });

  it('falls back when reply empty after strip', () => {
    expect(previewGenerationReply('<think>only</think>')).toBe('Ответ готов');
  });

  it('formats toast with chat title', () => {
    expect(formatGenerationCompleteToast('Мой чат', 'Короткий ответ')).toBe(
      'Мой чат: Короткий ответ',
    );
  });

  it('uses default title when chat title missing', () => {
    expect(generationCompleteToastTitle('')).toBe('Чат');
    expect(formatGenerationCompleteToast('', 'Текст')).toBe('Чат: Текст');
  });

  it('shows toast only when viewing another chat', () => {
    expect(shouldShowGenerationCompleteToast('a', 'b')).toBe(true);
    expect(shouldShowGenerationCompleteToast('a', 'a')).toBe(false);
    expect(shouldShowGenerationCompleteToast('a', null)).toBe(true);
    expect(shouldShowGenerationCompleteToast('', 'b')).toBe(false);
  });

  it('dismisses after 20 seconds', () => {
    expect(GENERATION_COMPLETE_TOAST_DISMISS_MS).toBe(20_000);
  });
});
