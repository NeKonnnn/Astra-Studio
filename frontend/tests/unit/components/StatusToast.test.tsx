import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import StatusToast from '@/components/StatusToast';
import { STATUS_TOAST_AUTO_DISMISS_MS } from '@/constants/statusToast';

describe('StatusToast', () => {
  beforeEach(() => {
    jest.useFakeTimers('modern');
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  it('renders nothing for an empty or blank message', () => {
    const { container, rerender } = render(<StatusToast type="info" message="" />);
    expect(container.firstChild).toBeNull();

    rerender(<StatusToast type="info" message="   " />);
    expect(container.firstChild).toBeNull();
  });

  it('shows the message', () => {
    render(<StatusToast type="success" message="Готово" />);

    expect(screen.getByText('Готово')).toBeTruthy();
  });

  it.each([
    ['error', 'alert'],
    ['warning', 'alert'],
    ['success', 'status'],
    ['info', 'status'],
  ] as const)('uses role=%s -> %s for assistive tech', (type, role) => {
    render(<StatusToast type={type} message="Сообщение" />);

    expect(screen.getByRole(role)).toBeTruthy();
  });

  it('has no close button without onClose', () => {
    render(<StatusToast type="info" message="Сообщение" />);

    expect(screen.queryByRole('button')).toBeNull();
  });

  it('calls onClose from the close button with a custom label', () => {
    const onClose = jest.fn();
    render(<StatusToast type="info" message="Сообщение" onClose={onClose} ariaLabel="Закрыть это" />);

    fireEvent.click(screen.getByRole('button', { name: 'Закрыть это' }));

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('dismisses itself after the default delay', () => {
    const onClose = jest.fn();
    render(<StatusToast type="info" message="Сообщение" onClose={onClose} />);

    act(() => {
      jest.advanceTimersByTime(STATUS_TOAST_AUTO_DISMISS_MS - 1);
    });
    expect(onClose).not.toHaveBeenCalled();

    act(() => {
      jest.advanceTimersByTime(1);
    });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('honours a custom dismiss delay', () => {
    const onClose = jest.fn();
    render(<StatusToast type="info" message="Сообщение" onClose={onClose} autoDismissMs={500} />);

    act(() => {
      jest.advanceTimersByTime(500);
    });

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('never dismisses itself when autoDismissMs is null or there is no onClose', () => {
    const onClose = jest.fn();
    render(<StatusToast type="info" message="A" onClose={onClose} autoDismissMs={null} />);
    render(<StatusToast type="info" message="B" />);

    act(() => {
      jest.advanceTimersByTime(STATUS_TOAST_AUTO_DISMISS_MS * 3);
    });

    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByText('A')).toBeTruthy();
    expect(screen.getByText('B')).toBeTruthy();
  });

  it('restarts the timer when the message changes', () => {
    const onClose = jest.fn();
    const { rerender } = render(<StatusToast type="info" message="Первое" onClose={onClose} autoDismissMs={1000} />);

    act(() => {
      jest.advanceTimersByTime(900);
    });
    rerender(<StatusToast type="info" message="Второе" onClose={onClose} autoDismissMs={1000} />);
    act(() => {
      jest.advanceTimersByTime(900);
    });
    expect(onClose).not.toHaveBeenCalled();

    act(() => {
      jest.advanceTimersByTime(100);
    });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('calls the latest onClose, not the one from the first render', () => {
    const first = jest.fn();
    const second = jest.fn();
    const { rerender } = render(<StatusToast type="info" message="М" onClose={first} autoDismissMs={1000} />);
    rerender(<StatusToast type="info" message="М" onClose={second} autoDismissMs={1000} />);

    act(() => {
      jest.advanceTimersByTime(1000);
    });

    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledTimes(1);
  });

  it('clears its timer on unmount', () => {
    const onClose = jest.fn();
    const { unmount } = render(<StatusToast type="info" message="М" onClose={onClose} autoDismissMs={1000} />);

    unmount();
    act(() => {
      jest.advanceTimersByTime(5000);
    });

    expect(onClose).not.toHaveBeenCalled();
  });
});
