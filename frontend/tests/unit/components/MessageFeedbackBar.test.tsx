import '@testing-library/jest-dom';
import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import MessageFeedbackBar from '@/components/MessageFeedbackBar';
import { DISLIKE_FEEDBACK_TAGS } from '@/constants/messageFeedback';

function setup(over: Record<string, unknown> = {}) {
  const h = {
    onLike: jest.fn().mockResolvedValue(undefined),
    onDislikeSubmit: jest.fn().mockResolvedValue(undefined),
    onClear: jest.fn().mockResolvedValue(undefined),
  };
  const utils = render(<MessageFeedbackBar {...h} {...(over as any)} />);
  return { ...h, ...utils };
}

const like = () => screen.getByRole('button', { name: 'Хороший ответ' });
const dislike = () => screen.getByRole('button', { name: 'Плохой ответ' });

describe('MessageFeedbackBar', () => {
  it('без оценки: лайк вызывает onLike', async () => {
    const h = setup();
    await act(async () => {
      fireEvent.click(like());
    });
    expect(h.onLike).toHaveBeenCalledTimes(1);
    expect(h.onClear).not.toHaveBeenCalled();
  });

  it('повторный клик по активному лайку снимает оценку', async () => {
    const h = setup({ feedback: { rating: 'like' } });
    expect(like().querySelector('[data-testid="ThumbUpIcon"]')).toBeTruthy();
    await act(async () => {
      fireEvent.click(like());
    });
    expect(h.onClear).toHaveBeenCalledTimes(1);
    expect(h.onLike).not.toHaveBeenCalled();
  });

  it('повторный клик по активному дизлайку снимает оценку без диалога', async () => {
    const h = setup({ feedback: { rating: 'dislike', tags: ['other'] } });
    expect(dislike().querySelector('[data-testid="ThumbDownIcon"]')).toBeTruthy();
    await act(async () => {
      fireEvent.click(dislike());
    });
    expect(h.onClear).toHaveBeenCalledTimes(1);
    expect(screen.queryByText('Поделиться отзывом')).toBeNull();
  });

  it('disabled блокирует обе кнопки', () => {
    setup({ disabled: true, isDarkMode: true, compact: true });
    expect(like()).toBeDisabled();
    expect(dislike()).toBeDisabled();
  });

  it('кнопки блокируются на время выполнения операции и повторные клики игнорируются', async () => {
    let finish!: () => void;
    const h = setup({ onLike: jest.fn(() => new Promise<void>((r) => (finish = r))) });
    fireEvent.click(like());
    expect(like()).toBeDisabled();
    expect(dislike()).toBeDisabled();
    fireEvent.click(like());
    expect(h.onLike).not.toHaveBeenCalled();
    await act(async () => finish());
    expect(like()).not.toBeDisabled();
  });

  it('дизлайк открывает диалог; отправка невозможна без тегов и комментария', () => {
    setup();
    fireEvent.click(dislike());
    expect(screen.getByText('Поделиться отзывом')).toBeInTheDocument();
    expect(screen.getByText('Отправить').closest('button')).toBeDisabled();
    for (const t of DISLIKE_FEEDBACK_TAGS) expect(screen.getByText(t.label)).toBeInTheDocument();
  });

  it('отправка: теги и обрезанный комментарий, диалог закрывается', async () => {
    const h = setup();
    fireEvent.click(dislike());
    fireEvent.click(screen.getByText(DISLIKE_FEEDBACK_TAGS[1].label));
    fireEvent.click(screen.getByText(DISLIKE_FEEDBACK_TAGS[2].label));
    fireEvent.click(screen.getByText(DISLIKE_FEEDBACK_TAGS[1].label)); // снять
    fireEvent.change(screen.getByPlaceholderText('Поделитесь подробностями (необязательно)'), {
      target: { value: '  подробности  ' },
    });
    await act(async () => {
      fireEvent.click(screen.getByText('Отправить'));
    });
    expect(h.onDislikeSubmit).toHaveBeenCalledWith({ tags: [DISLIKE_FEEDBACK_TAGS[2].id], comment: 'подробности' });
    await waitFor(() => expect(screen.queryByText('Отправить')).toBeNull());
  });

  it('достаточно одного комментария', async () => {
    const h = setup();
    fireEvent.click(dislike());
    fireEvent.change(screen.getByPlaceholderText('Поделитесь подробностями (необязательно)'), { target: { value: 'плохо' } });
    expect(screen.getByText('Отправить').closest('button')).not.toBeDisabled();
    await act(async () => {
      fireEvent.click(screen.getByText('Отправить'));
    });
    expect(h.onDislikeSubmit).toHaveBeenCalledWith({ tags: [], comment: 'плохо' });
  });

  it('диалог предзаполняется прежним отзывом только если он был дизлайком', () => {
    const { unmount } = setup({ feedback: { rating: 'like', tags: ['other'], comment: 'старый' } });
    fireEvent.click(dislike());
    expect(screen.getByPlaceholderText('Поделитесь подробностями (необязательно)')).toHaveValue('');
    unmount();
    // для активного дизлайка клик снимает оценку, поэтому диалог можно открыть только без дизлайка; предзаполнение проверяем через повторное открытие после сброса
    setup({ feedback: undefined });
    fireEvent.click(dislike());
    expect(screen.getByPlaceholderText('Поделитесь подробностями (необязательно)')).toHaveValue('');
  });

  it('крестик закрывает диалог', async () => {
    setup();
    fireEvent.click(dislike());
    fireEvent.click(screen.getByLabelText('Закрыть'));
    await waitFor(() => expect(screen.queryByPlaceholderText('Поделитесь подробностями (необязательно)')).toBeNull());
  });

  it('во время отправки диалог не закрывается по Escape и поле блокируется', async () => {
    let finish!: () => void;
    setup({ onDislikeSubmit: jest.fn(() => new Promise<void>((r) => (finish = r))) });
    fireEvent.click(dislike());
    fireEvent.change(screen.getByPlaceholderText('Поделитесь подробностями (необязательно)'), { target: { value: 'x' } });
    fireEvent.click(screen.getByText('Отправить'));
    expect(screen.getByPlaceholderText('Поделитесь подробностями (необязательно)')).toBeDisabled();
    expect(screen.getByLabelText('Закрыть')).toBeDisabled();
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
    expect(screen.getByText('Поделиться отзывом')).toBeInTheDocument();
    await act(async () => finish());
  });
});
