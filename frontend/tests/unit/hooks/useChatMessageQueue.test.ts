import { act, renderHook } from '@testing-library/react';
import { useChatMessageQueue } from '@/hooks/useChatMessageQueue';

describe('useChatMessageQueue', () => {
  it('enqueue / shift / remove / update работают per chatId', () => {
    const { result } = renderHook(() => useChatMessageQueue());

    act(() => {
      result.current.enqueue('c1', { text: 'a', inlineAttachments: [] });
      result.current.enqueue('c1', { text: 'b', inlineAttachments: [] });
      result.current.enqueue('c2', { text: 'x', inlineAttachments: [] });
    });

    expect(result.current.getQueue('c1')).toHaveLength(2);
    expect(result.current.peek('c1')?.text).toBe('a');

    let first: { text: string; id: string } | undefined;
    act(() => {
      first = result.current.shift('c1');
    });
    expect(first?.text).toBe('a');
    expect(result.current.getQueue('c1')).toHaveLength(1);

    const restId = result.current.getQueue('c1')[0].id;
    act(() => {
      result.current.update('c1', restId, { text: 'b-edited' });
    });
    expect(result.current.getQueue('c1')[0].text).toBe('b-edited');

    act(() => {
      result.current.remove('c1', restId);
    });
    expect(result.current.getQueue('c1')).toHaveLength(0);
    expect(result.current.getQueue('c2')).toHaveLength(1);
  });

  it('beginDrain / endDrain сериализуют дренаж', () => {
    const { result } = renderHook(() => useChatMessageQueue());
    expect(result.current.beginDrain('c1')).toBe(true);
    expect(result.current.beginDrain('c1')).toBe(false);
    act(() => {
      result.current.endDrain('c1');
    });
    expect(result.current.beginDrain('c1')).toBe(true);
  });
});
