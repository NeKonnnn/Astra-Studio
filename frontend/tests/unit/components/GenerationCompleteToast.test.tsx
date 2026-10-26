/**
 * @jest-environment jsdom
 */
import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import GenerationCompleteToast from '@/components/GenerationCompleteToast';

describe('GenerationCompleteToast', () => {
  beforeEach(() => {
    jest.useFakeTimers();
  });
  afterEach(() => {
    jest.useRealTimers();
  });

  it('renders title, preview, close button and opens on click', () => {
    const onOpen = jest.fn();
    const onClose = jest.fn();
    render(
      <GenerationCompleteToast
        title="RUJP Русско-японская война"
        preview="О русско-японской войне…"
        onOpen={onOpen}
        onClose={onClose}
        autoDismissMs={20_000}
      />,
    );
    expect(screen.getByText('RUJP Русско-японская война')).toBeInTheDocument();
    expect(screen.getByText('О русско-японской войне…')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('status'));
    expect(onOpen).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByLabelText('Закрыть уведомление'));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('auto-dismisses after 20s', () => {
    const onClose = jest.fn();
    render(
      <GenerationCompleteToast
        title="Чат"
        preview="Ответ"
        onOpen={jest.fn()}
        onClose={onClose}
        autoDismissMs={20_000}
      />,
    );
    act(() => {
      jest.advanceTimersByTime(19_999);
    });
    expect(onClose).not.toHaveBeenCalled();
    act(() => {
      jest.advanceTimersByTime(1);
    });
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
