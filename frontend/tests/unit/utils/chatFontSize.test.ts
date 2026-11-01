import {
  CHAT_FONT_SIZE_EVENT,
  CHAT_FONT_SIZE_KEY,
  getChatFontSize,
  getChatFontSizeValue,
  setChatFontSize,
} from '@/utils/chatFontSize';

describe('chatFontSize', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('defaults to medium', () => {
    expect(getChatFontSize()).toBe('medium');
  });

  it.each(['small', 'medium', 'large'] as const)('reads a saved %s size', (size) => {
    localStorage.setItem(CHAT_FONT_SIZE_KEY, size);

    expect(getChatFontSize()).toBe(size);
  });

  it('ignores an unknown saved value', () => {
    localStorage.setItem(CHAT_FONT_SIZE_KEY, 'huge');

    expect(getChatFontSize()).toBe('medium');
  });

  it('maps sizes to rem values', () => {
    expect(getChatFontSizeValue('small')).toBe('0.875rem');
    expect(getChatFontSizeValue('medium')).toBe('1rem');
    expect(getChatFontSizeValue('large')).toBe('1.125rem');
  });

  it('persists the size and notifies listeners', () => {
    const handler = jest.fn();
    window.addEventListener(CHAT_FONT_SIZE_EVENT, handler);

    setChatFontSize('large');
    window.removeEventListener(CHAT_FONT_SIZE_EVENT, handler);

    expect(localStorage.getItem(CHAT_FONT_SIZE_KEY)).toBe('large');
    expect(handler).toHaveBeenCalledTimes(1);
    expect((handler.mock.calls[0][0] as CustomEvent).detail).toBe('large');
    expect(getChatFontSize()).toBe('large');
  });
});
