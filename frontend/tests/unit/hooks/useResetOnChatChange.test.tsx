import React, { StrictMode, useEffect, useState } from 'react';
import { render, renderHook } from '@testing-library/react';
import '@testing-library/jest-dom';
import { useResetOnChatChange } from '@/hooks/useResetOnChatChange';

describe('useResetOnChatChange (BUG-FE-21)', () => {
  it('не вызывается на монтировании', () => {
    const reset = jest.fn();
    renderHook(() => useResetOnChatChange('chat-1', reset));
    expect(reset).not.toHaveBeenCalled();
  });

  it('вызывается при смене идентификатора чата', () => {
    const reset = jest.fn();
    const { rerender } = renderHook(({ id }) => useResetOnChatChange(id, reset), {
      initialProps: { id: 'a' as string | null },
    });
    rerender({ id: 'b' });
    expect(reset).toHaveBeenCalledTimes(1);
    rerender({ id: null });
    expect(reset).toHaveBeenCalledTimes(2);
  });

  it('не вызывается при повторном рендере с тем же id', () => {
    const reset = jest.fn();
    const { rerender } = renderHook(({ id }) => useResetOnChatChange(id, reset), {
      initialProps: { id: 'a' as string | null },
    });
    rerender({ id: 'a' });
    expect(reset).not.toHaveBeenCalled();
  });

  it('использует актуальный колбэк', () => {
    const first = jest.fn();
    const second = jest.fn();
    const { rerender } = renderHook(({ id, cb }) => useResetOnChatChange(id, cb), {
      initialProps: { id: 'a' as string | null, cb: first },
    });
    rerender({ id: 'b', cb: second });
    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledTimes(1);
  });

  it('отложенный текст, вставленный эффектом до сброса, переживает монтирование (в т.ч. StrictMode)', () => {
    function Probe({ chatId }: { chatId: string | null }) {
      const [text, setText] = useState('');
      // эффект вставки объявлен раньше эффекта сброса - как в UnifiedChatPage
      useEffect(() => {
        setText('отложенный текст');
      }, []);
      useResetOnChatChange(chatId, () => setText(''));
      return <input data-testid="f" value={text} readOnly />;
    }

    const { getByTestId, rerender } = render(
      <StrictMode>
        <Probe chatId="c1" />
      </StrictMode>,
    );
    expect(getByTestId('f')).toHaveValue('отложенный текст');

    rerender(
      <StrictMode>
        <Probe chatId="c2" />
      </StrictMode>,
    );
    expect(getByTestId('f')).toHaveValue('');
  });
});
