import React from 'react';
import { render, screen, cleanup } from '@testing-library/react';
import StatusToast from './StatusToast';
import {
  STATUS_TOAST_STACK_ID,
  STATUS_TOAST_STACK_TOP_PX,
  STATUS_TOAST_STACK_Z_INDEX,
} from '../constants/statusToast';

afterEach(() => {
  cleanup();
  document.getElementById(STATUS_TOAST_STACK_ID)?.remove();
});

describe('StatusToast: общий стек', () => {
  it('fixed-тосты встают в один контейнер-столбик, а не накладываются друг на друга', () => {
    render(
      <>
        <StatusToast fixed type="error" message="Ошибка LLM" onClose={() => {}} autoDismissMs={null} />
        <StatusToast fixed type="success" message="Соединение установлено" onClose={() => {}} autoDismissMs={null} />
        <StatusToast fixed type="warning" message="Перечанковка RAG" onClose={() => {}} autoDismissMs={null} />
      </>,
    );
    const stack = document.getElementById(STATUS_TOAST_STACK_ID) as HTMLElement;
    expect(stack).not.toBeNull();
    expect(document.querySelectorAll(`#${STATUS_TOAST_STACK_ID}`)).toHaveLength(1);
    expect(stack.style.display).toBe('flex');
    expect(stack.style.flexDirection).toBe('column');
    expect(stack.style.position).toBe('fixed');
    expect(Number(stack.style.zIndex)).toBe(STATUS_TOAST_STACK_Z_INDEX);
    expect(stack.children).toHaveLength(3);
    // Порядок в стеке — порядок появления.
    expect(Array.from(stack.children).map((c) => c.textContent)).toEqual([
      expect.stringContaining('Ошибка LLM'),
      expect.stringContaining('Соединение установлено'),
      expect.stringContaining('Перечанковка RAG'),
    ]);
  });

  it('стек стоит ниже шапки (не перекрывает кнопки сверху)', () => {
    render(<StatusToast fixed type="info" message="x" autoDismissMs={null} />);
    const stack = document.getElementById(STATUS_TOAST_STACK_ID) as HTMLElement;
    expect(STATUS_TOAST_STACK_TOP_PX).toBeGreaterThanOrEqual(56);
    expect(stack.style.top).toBe(`${STATUS_TOAST_STACK_TOP_PX}px`);
  });

  it('стек не перехватывает клики, а сам тост — принимает', () => {
    render(<StatusToast fixed type="error" message="Ошибка" onClose={() => {}} autoDismissMs={null} />);
    const stack = document.getElementById(STATUS_TOAST_STACK_ID) as HTMLElement;
    expect(stack.style.pointerEvents).toBe('none');
    expect(getComputedStyle(screen.getByRole('alert')).pointerEvents).toBe('auto');
  });

  it('стек ограничен шириной экрана (узкие экраны)', () => {
    render(<StatusToast fixed type="info" message="Привет" autoDismissMs={null} />);
    const stack = document.getElementById(STATUS_TOAST_STACK_ID) as HTMLElement;
    expect(stack.style.maxWidth).toBe('calc(100vw - 24px)');
  });

  it('без fixed тост рендерится на месте и стек не создаётся', () => {
    const { container } = render(<StatusToast type="info" message="Скопировано" autoDismissMs={null} />);
    expect(document.getElementById(STATUS_TOAST_STACK_ID)).toBeNull();
    expect(container.textContent).toContain('Скопировано');
  });

  it('пустое сообщение ничего не рисует', () => {
    render(<StatusToast fixed type="info" message="   " autoDismissMs={null} />);
    expect(document.getElementById(STATUS_TOAST_STACK_ID)).toBeNull();
  });

  it('роль: ошибка/предупреждение — alert, остальное — status', () => {
    render(
      <>
        <StatusToast fixed type="error" message="e" autoDismissMs={null} />
        <StatusToast fixed type="warning" message="w" autoDismissMs={null} />
        <StatusToast fixed type="success" message="s" autoDismissMs={null} />
      </>,
    );
    expect(screen.getAllByRole('alert')).toHaveLength(2);
    expect(screen.getAllByRole('status')).toHaveLength(1);
  });

  it('автоскрытие вызывает onClose', () => {
    jest.useFakeTimers();
    const onClose = jest.fn();
    render(<StatusToast fixed type="info" message="x" onClose={onClose} autoDismissMs={1000} />);
    jest.advanceTimersByTime(1000);
    expect(onClose).toHaveBeenCalledTimes(1);
    jest.useRealTimers();
  });
});
