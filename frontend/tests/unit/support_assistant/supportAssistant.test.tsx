import '@testing-library/jest-dom';
import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import SupportAssistantWidget from '@/support_assistant/SupportAssistantWidget';
import DefaultFromIndex, { SupportAssistantWidget as Named } from '@/support_assistant/index';
import {
  DOC_MEDIA_BASE,
  SUPPORT_ARTICLES,
  findSupportArticle,
  formatArticleReply,
  mediaUrl,
  type SupportArticle,
} from '@/support_assistant/knowledgeArticles';

jest.mock('react-markdown', () => ({
  __esModule: true,
  default: ({ children }: { children: string }) => <div data-testid="md">{children}</div>,
}));

jest.mock('@/hooks/useRightSidebarInsetCssVar', () => ({
  useRightRailOffsetPx: () => 40,
}));

describe('knowledgeArticles', () => {
  it('mediaUrl строит путь по типу медиа', () => {
    expect(mediaUrl({ kind: 'screen', file: 'a.png' })).toBe(`${DOC_MEDIA_BASE}/screens/a.png`);
    expect(mediaUrl({ kind: 'gif', file: 'a.gif' })).toBe(`${DOC_MEDIA_BASE}/gif/a.gif`);
  });

  it('база не пуста, у статей уникальные id и есть шаги', () => {
    expect(SUPPORT_ARTICLES.length).toBeGreaterThan(50);
    const ids = SUPPORT_ARTICLES.map((a) => a.id);
    expect(new Set(ids).size).toBe(ids.length);
    expect(SUPPORT_ARTICLES.every((a) => a.steps.length > 0 && a.title)).toBe(true);
  });

  it('findSupportArticle: пустой запрос и непонятный запрос дают fallback', () => {
    expect(findSupportArticle('   ').id).toBe('fallback');
    expect(findSupportArticle('абракадабра zzz').id).toBe('fallback');
  });

  it('findSupportArticle: выбирает статью по ключевой фразе без учёта регистра', () => {
    expect(findSupportArticle('Как СОЗДАТЬ ЧАТ?').id).toBe('create-chat');
  });

  it('findSupportArticle: побеждает статья с наибольшей суммарной длиной совпавших ключей', () => {
    const a = SUPPORT_ARTICLES.find((x) => x.id === 'create-chat')!;
    const kw = a.keywords.find((k) => k === 'как создать чат')!;
    // фраза содержит и «создать чат», и «как создать чат» — суммарный вес выше любой одиночной статьи
    expect(findSupportArticle(kw).id).toBe('create-chat');
  });

  it('formatArticleReply: заголовок, нумерация, подсказки и медиа с подписями', () => {
    const art: SupportArticle = {
      id: 'x',
      keywords: [],
      title: 'Заголовок',
      steps: ['Шаг А', 'Шаг Б'],
      uiHints: ['Слева'],
      media: [
        { kind: 'screen', file: 's.png', alt: 'Альт', caption: 'Подпись' },
        { kind: 'gif', file: 'g.gif' },
        { kind: 'screen', file: 'c.png', caption: 'Только подпись' },
      ],
    };
    const text = formatArticleReply(art);
    expect(text.split('\n').slice(0, 5)).toEqual(['**Заголовок**', '', '1. Шаг А', '2. Шаг Б', '']);
    expect(text).toContain('_Куда смотреть:_\n• Слева');
    expect(text).toContain('_Подпись_\n![Альт](/static/user_documentation/screens/s.png)');
    expect(text).toContain('![g.gif](/static/user_documentation/gif/g.gif)');
    expect(text).toContain('![Только подпись](/static/user_documentation/screens/c.png)');
  });

  it('formatArticleReply: без подсказок и медиа блоков нет', () => {
    const text = formatArticleReply({ id: 'x', keywords: [], title: 'T', steps: ['a'] });
    expect(text).toBe('**T**\n\n1. a');
  });
});

describe('SupportAssistantWidget', () => {
  beforeEach(() => jest.useFakeTimers());
  afterEach(() => jest.useRealTimers());

  it('index реэкспортирует виджет', () => {
    expect(DefaultFromIndex).toBe(SupportAssistantWidget);
    expect(Named).toBe(SupportAssistantWidget);
  });

  it('свёрнут по умолчанию: только кнопка «Открыть помощь»', () => {
    render(<SupportAssistantWidget />);
    expect(screen.getByLabelText('Открыть помощь')).toBeInTheDocument();
    expect(screen.queryByPlaceholderText('Например: как открыть настройки')).toBeNull();
  });

  it('открытие показывает приветствие и фокусирует поле ввода', () => {
    render(<SupportAssistantWidget />);
    fireEvent.click(screen.getByLabelText('Открыть помощь'));
    expect(screen.getByText(/Чем могу помочь/)).toBeInTheDocument();
    act(() => {
      jest.advanceTimersByTime(200);
    });
    expect(document.activeElement).toBe(screen.getByPlaceholderText('Например: как открыть настройки'));
    expect(screen.getByLabelText('Свернуть помощь')).toBeInTheDocument();
  });

  it('вопрос -> индикатор поиска -> ответ из базы; поле блокируется на время ответа', async () => {
    render(<SupportAssistantWidget />);
    fireEvent.click(screen.getByLabelText('Открыть помощь'));
    const input = screen.getByPlaceholderText('Например: как открыть настройки');
    fireEvent.change(input, { target: { value: 'как создать чат' } });
    fireEvent.click(screen.getByLabelText('Отправить'));
    expect(screen.getByText('как создать чат')).toBeInTheDocument();
    expect(screen.getByText('Ищу инструкцию…')).toBeInTheDocument();
    expect(input).toBeDisabled();
    await act(async () => {
      jest.advanceTimersByTime(500);
    });
    expect(screen.queryByText('Ищу инструкцию…')).toBeNull();
    const replies = screen.getAllByTestId('md').map((n) => n.textContent || '');
    expect(replies.some((t) => t.includes('**Как создать новый чат**'))).toBe(true);
    expect((screen.getByPlaceholderText('Например: как открыть настройки') as HTMLInputElement).value).toBe('');
  });

  it('Enter отправляет, Shift+Enter нет; пустой ввод не отправляется', async () => {
    render(<SupportAssistantWidget />);
    fireEvent.click(screen.getByLabelText('Открыть помощь'));
    const input = screen.getByPlaceholderText('Например: как открыть настройки');
    expect(screen.getByLabelText('Отправить')).toBeDisabled();
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(screen.queryByText('Ищу инструкцию…')).toBeNull();
    fireEvent.change(input, { target: { value: 'абракадабра' } });
    fireEvent.keyDown(input, { key: 'Enter', shiftKey: true });
    expect(screen.queryByText('Ищу инструкцию…')).toBeNull();
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(screen.getByText('Ищу инструкцию…')).toBeInTheDocument();
    await act(async () => {
      jest.advanceTimersByTime(500);
    });
    const replies = screen.getAllByTestId('md').map((n) => n.textContent || '');
    expect(replies.some((t) => t.includes('Не нашёл точную инструкцию'))).toBe(true);
  });

  it('ошибка загрузки картинки помощника заменяет её иконкой', () => {
    render(<SupportAssistantWidget />);
    fireEvent.click(screen.getByLabelText('Открыть помощь'));
    const img = screen.getByAltText('Помощник');
    expect(img).toHaveAttribute('src', '/static/support/assistant.png');
    fireEvent.error(img);
    expect(screen.queryByAltText('Помощник')).toBeNull();
    expect(document.querySelector('[data-testid="SupportAgentIcon"]')).toBeTruthy();
  });

  it('кнопка «Свернуть» в шапке закрывает панель', () => {
    render(<SupportAssistantWidget />);
    fireEvent.click(screen.getByLabelText('Открыть помощь'));
    fireEvent.click(screen.getByLabelText('Свернуть'));
    act(() => {
      jest.advanceTimersByTime(400);
    });
    expect(screen.queryByPlaceholderText('Например: как открыть настройки')).toBeNull();
  });
});
