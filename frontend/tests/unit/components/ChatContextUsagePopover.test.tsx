import '@testing-library/jest-dom';
import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import ChatContextUsagePopover from '@/components/ChatContextUsagePopover';

const seg = (id: string, label: string, tokens: number, active?: boolean) => ({ id, label, tokens, active });

function usage(over: Record<string, unknown> = {}) {
  return {
    currentTokens: 3200,
    maxTokens: 8192,
    percent: 39,
    messageTokens: 3000,
    draftTokens: 200,
    attachmentTokens: 0,
    isOverLimit: false,
    overheadLoading: false,
    refreshOverhead: jest.fn(),
    segments: [
      seg('memory', 'Память', 1500),
      seg('agent', 'Агент', 12345, false),
      seg('current_user', 'Текущее', 5),
      seg('project', 'Проект', 0),
      seg('unknown_segment', 'Неизвестный', 10),
    ],
    ...over,
  } as any;
}

describe('ChatContextUsagePopover', () => {
  it('кольцо показывает процент и подпись доступности', () => {
    render(<ChatContextUsagePopover usage={usage()} modelLabel="GPT" />);
    const btn = screen.getByRole('button', { name: 'Использование контекста 39%' });
    expect(btn).toHaveTextContent('39%');
    expect(screen.getAllByRole('progressbar')).toHaveLength(2);
    expect(screen.getAllByRole('progressbar')[1]).toHaveAttribute('aria-valuenow', '39');
  });

  it('процент выше 100 ограничивается в кольце, цвета для порогов и тем', () => {
    const colors = new Set<string>();
    for (const [percent, dark] of [[10, false], [75, false], [120, false], [10, true], [75, true], [120, true]] as const) {
      const { unmount } = render(<ChatContextUsagePopover usage={usage({ percent })} isDarkMode={dark} />);
      colors.add(getComputedStyle(screen.getByText(`${percent}%`)).color);
      if (percent === 120) {
        expect(screen.getAllByRole('progressbar')[1]).toHaveAttribute('aria-valuenow', '100');
      }
      unmount();
    }
    expect(colors.size).toBe(6);
  });

  it('подсказка кнопки содержит модель, токены и процент', async () => {
    render(<ChatContextUsagePopover usage={usage()} modelLabel="Модель-1" />);
    fireEvent.mouseOver(screen.getByRole('button', { name: /Использование контекста/ }));
    const tip = await screen.findByRole('tooltip');
    expect(tip).toHaveTextContent('Модель: Модель-1');
    expect(tip).toHaveTextContent('(39%)');
    expect(tip).toHaveTextContent('Нажмите для детализации');
  });

  it('клик открывает детализацию и запрашивает обновление системных сегментов', () => {
    const u = usage();
    render(<ChatContextUsagePopover usage={u} />);
    expect(screen.queryByText('Использование контекста')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /Использование контекста/ }));
    expect(u.refreshOverhead).toHaveBeenCalledTimes(1);
    expect(screen.getByText('Использование контекста')).toBeInTheDocument();
    expect(screen.getByText('39% заполнено')).toBeInTheDocument();
  });

  it('сегменты идут в заданном порядке, неактивные помечены, неизвестные id не показываются', () => {
    render(<ChatContextUsagePopover usage={usage()} isDarkMode />);
    fireEvent.click(screen.getByRole('button', { name: /Использование контекста/ }));
    const labels = Array.from(document.querySelectorAll('.MuiPopover-paper p.MuiTypography-body2')).map((n) => n.textContent);
    expect(labels.filter((t) => /Проект|Агент|Память|Текущее/.test(t || ''))).toEqual([
      'Проект',
      'Агент (не активен)',
      'Память',
      'Текущее',
    ]);
    expect(screen.queryByText('Неизвестный')).toBeNull();
  });

  it('форматирование токенов сегментов: тысячи с «K», запятая в дробной части', () => {
    render(
      <ChatContextUsagePopover
        usage={usage({
          segments: [seg('memory', 'А', 12345), seg('agent', 'Б', 2000), seg('project', 'В', 999), seg('skills', 'Г', 1500)],
        })}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /Использование контекста/ }));
    expect(screen.getByText('12,3K')).toBeInTheDocument();
    expect(screen.getByText('2K')).toBeInTheDocument();
    expect(screen.getByText('999')).toBeInTheDocument();
    expect(screen.getByText('1,5K')).toBeInTheDocument();
  });

  it('полоса: только сегменты с токенами, у неактивных приглушённый цвет; нулевой общий размер не делит на 0', () => {
    render(<ChatContextUsagePopover usage={usage({ currentTokens: 0 })} />);
    fireEvent.click(screen.getByRole('button', { name: /Использование контекста/ }));
    const bar = Array.from(document.querySelectorAll('.MuiPopover-paper [title]')).map((n) => n.getAttribute('title'));
    expect(bar).toEqual(['Агент: 12345', 'Память: 1500', 'Текущее: 5']);
  });

  it('индикатор загрузки системных сегментов', () => {
    render(<ChatContextUsagePopover usage={usage({ overheadLoading: true, segments: undefined })} />);
    fireEvent.click(screen.getByRole('button', { name: /Использование контекста/ }));
    expect(screen.getByText('Загрузка системных сегментов…')).toBeInTheDocument();
  });

  it('превышение лимита меняет пояснение; цвет зависит от процента', () => {
    const { unmount } = render(<ChatContextUsagePopover usage={usage({ isOverLimit: true, percent: 105 })} />);
    fireEvent.click(screen.getByRole('button', { name: /Использование контекста/ }));
    expect(screen.getByText(/Оценка превышает окно модели/)).toBeInTheDocument();
    expect(screen.queryByText(/Память — все завершённые сообщения/)).toBeNull();
    unmount();
    const second = render(<ChatContextUsagePopover usage={usage({ isOverLimit: true, percent: 80 })} isDarkMode />);
    fireEvent.click(screen.getByRole('button', { name: /Использование контекста/ }));
    expect(screen.getByText(/Оценка превышает окно модели/)).toBeInTheDocument();
    second.unmount();
  });

  it('Escape закрывает поповер', async () => {
    render(<ChatContextUsagePopover usage={usage()} />);
    fireEvent.click(screen.getByRole('button', { name: /Использование контекста/ }));
    fireEvent.keyDown(document.querySelector('.MuiPopover-root')!, { key: 'Escape' });
    await new Promise((r) => setTimeout(r, 400));
    expect(screen.queryByText('Использование контекста')).toBeNull();
  });

  it('обычное пояснение без превышения; крестик закрывает поповер', async () => {
    render(<ChatContextUsagePopover usage={usage()} />);
    fireEvent.click(screen.getByRole('button', { name: /Использование контекста/ }));
    expect(screen.getByText(/Память — все завершённые сообщения/)).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText('Закрыть'));
    await new Promise((r) => setTimeout(r, 400));
    expect(screen.queryByText('Использование контекста')).toBeNull();
  });
});
