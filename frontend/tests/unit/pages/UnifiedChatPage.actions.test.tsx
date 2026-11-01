import React from 'react';
import { render, screen, fireEvent, act, within, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';
import {
  hx,
  resetHarness,
  installFetch,
  fakeResponse,
  msg,
  userMsg,
} from '@/pages/__test_support__/unifiedChatHarness';
import UnifiedChatPage from '@/pages/UnifiedChatPage';

function renderPage() {
  return render(<UnifiedChatPage isDarkMode={false} />);
}
const cards = (c: HTMLElement) => Array.from(c.querySelectorAll('[data-message-index]')) as HTMLElement[];
/** Кнопка по aria-label Tooltip (для disabled-кнопок label висит на обёртке span). */
function btn(card: HTMLElement, label: string): HTMLElement {
  const el = within(card).getByLabelText(label);
  return (el.tagName === 'SPAN' ? el.querySelector('button') : el) as HTMLElement;
}
const COPY_OK = 'Копирование в буфер обмена прошло успешно!';
const COPY_FAIL = 'Не удалось скопировать текст';

let clipboard: { writeText: jest.Mock; write: jest.Mock };

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  resetHarness();
  installFetch();
  clipboard = { writeText: jest.fn().mockResolvedValue(undefined), write: jest.fn().mockResolvedValue(undefined) };
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: clipboard });
});

afterEach(() => {
  delete (global as any).ClipboardItem;
  jest.useRealTimers();
});

describe('UnifiedChatPage: панель действий сообщения', () => {
  it('навигация по вариантам ответа вызывает updateMessage с нужными аргументами', () => {
    const att = [{ name: 'x.png', contentType: 'image' as const, preview: 'p' }];
    hx.messages = [
      msg({
        id: 'a1',
        content: 'b',
        alternativeResponses: ['a', 'b', 'c'],
        currentResponseIndex: 1,
        inlineAttachmentVariants: [[], [], att],
      }),
    ];
    const { container } = renderPage();
    const card = cards(container)[0];
    expect(within(card).getByText('2/3')).toBeInTheDocument();

    fireEvent.click(btn(card, 'Следующий вариант'));
    expect(hx.actions.updateMessage).toHaveBeenLastCalledWith(
      'c1', 'a1', 'c', undefined, undefined, ['a', 'b', 'c'], 2, undefined, undefined, undefined, att,
    );
    fireEvent.click(btn(card, 'Предыдущий вариант'));
    expect(hx.actions.updateMessage).toHaveBeenLastCalledWith(
      'c1', 'a1', 'a', undefined, undefined, ['a', 'b', 'c'], 0, undefined, undefined, undefined, undefined,
    );
  });

  it('на краях списка кнопки переключения вариантов отключены', () => {
    hx.messages = [msg({ id: 'a1', content: 'a', alternativeResponses: ['a', 'b'], currentResponseIndex: 0 })];
    const { container, unmount } = renderPage();
    expect(btn(cards(container)[0], 'Предыдущий вариант')).toBeDisabled();
    unmount();
    hx.messages = [msg({ id: 'a1', content: 'b', alternativeResponses: ['a', 'b'], currentResponseIndex: 1 })];
    const second = renderPage();
    expect(btn(cards(second.container)[0], 'Следующий вариант')).toBeDisabled();
  });

  it('копирование сообщения пользователя пишет plain-текст в буфер и показывает успех', async () => {
    hx.messages = [userMsg({ id: 'u1', content: 'вопрос' })];
    const { container } = renderPage();
    fireEvent.click(btn(cards(container)[0], 'Копировать'));
    await waitFor(() => expect(hx.actions.showNotification).toHaveBeenCalledWith('success', COPY_OK));
    expect(clipboard.writeText).toHaveBeenCalledWith('plain:вопрос');
  });

  it('копирование сообщения с multiLLMResponses (у пользователя) склеивает ответы колонок', async () => {
    hx.messages = [
      userMsg({
        id: 'u1',
        content: 'q',
        multiLLMResponses: [
          { model: 'm1', content: 'A' },
          { model: 'm2', content: 'B' },
        ],
      }),
    ];
    const { container } = renderPage();
    // у «пользовательской» карточки внешняя панель видна: первая кнопка «Копировать» — внешняя
    const copyButtons = within(cards(container)[0]).getAllByLabelText('Копировать');
    fireEvent.click(copyButtons[copyButtons.length - 1]);
    await waitFor(() => expect(clipboard.writeText).toHaveBeenCalled());
    expect(clipboard.writeText).toHaveBeenCalledWith('plain:[m1]\nA\n\n---\n\n[m2]\nB');
  });

  it('если доступен ClipboardItem, копирует через clipboard.write с text/plain и text/html', async () => {
    class FakeClipboardItem {
      items: Record<string, Blob>;
      constructor(items: Record<string, Blob>) {
        this.items = items;
      }
    }
    (global as any).ClipboardItem = FakeClipboardItem;
    hx.messages = [msg({ id: 'a1', content: '**жирный**' })];
    const { container } = renderPage();
    fireEvent.click(btn(cards(container)[0], 'Копировать'));
    await waitFor(() => expect(clipboard.write).toHaveBeenCalled());
    const item = clipboard.write.mock.calls[0][0][0];
    expect(Object.keys(item.items)).toEqual(['text/plain', 'text/html']);
    expect(hx.actions.showNotification).toHaveBeenCalledWith('success', COPY_OK);
  });

  it('при сбое богатого копирования откатывается на writeText(исходный текст)', async () => {
    (global as any).ClipboardItem = class {};
    clipboard.write.mockRejectedValue(new Error('denied'));
    hx.messages = [msg({ id: 'a1', content: 'сырой' })];
    const { container } = renderPage();
    fireEvent.click(btn(cards(container)[0], 'Копировать'));
    await waitFor(() => expect(hx.actions.showNotification).toHaveBeenCalledWith('success', COPY_OK));
    expect(clipboard.writeText).toHaveBeenCalledWith('сырой');
  });

  it('если и запасной вариант не сработал, показывает ошибку копирования', async () => {
    clipboard.writeText.mockRejectedValue(new Error('denied'));
    hx.messages = [msg({ id: 'a1', content: 'сырой' })];
    const { container } = renderPage();
    fireEvent.click(btn(cards(container)[0], 'Копировать'));
    await waitFor(() => expect(hx.actions.showNotification).toHaveBeenCalledWith('error', COPY_FAIL));
  });

  it('кнопки «Поделиться» и «Перегенерировать» есть только у ассистента; у пользователя — нет', () => {
    hx.messages = [userMsg({ id: 'u1' }), msg({ id: 'a1' })];
    const { container } = renderPage();
    const [u, a] = cards(container);
    expect(within(u).queryByLabelText('Поделиться')).toBeNull();
    expect(within(u).queryByLabelText('Перегенерировать')).toBeNull();
    expect(within(u).queryByTestId('feedback-bar')).toBeNull();
    expect(within(a).getByLabelText('Поделиться')).toBeInTheDocument();
    expect(within(a).getByTestId('feedback-bar')).toBeInTheDocument();
  });

  it('у потокового ассистента нет панели оценки и экспорт/ветка отключены', () => {
    hx.messages = [msg({ id: 'a1', content: 'идёт', isStreaming: true })];
    const { container } = renderPage();
    const card = cards(container)[0];
    expect(within(card).queryByTestId('feedback-bar')).toBeNull();
    const menu = within(card).getByTestId('more-menu');
    expect(menu).toHaveAttribute('data-branch-disabled', 'true');
    expect(menu).toHaveAttribute('data-export-disabled', 'true');
  });

  it('экспорт отключён для пустого текста, включён для непустого; у пользователя экспорта нет', () => {
    hx.messages = [
      userMsg({ id: 'u1' }),
      msg({ id: 'a1', content: '   ' }),
      msg({ id: 'a2', content: 'текст' }),
      msg({ id: 'a3', content: 'x', alternativeResponses: ['', 'вариант'], currentResponseIndex: 0 }),
    ];
    const { container } = renderPage();
    const menus = cards(container).map((c) => within(c).getByTestId('more-menu'));
    expect(menus[0]).toHaveAttribute('data-has-export', 'false');
    expect(menus[0]).toHaveAttribute('data-show-branch', 'false');
    expect(menus[1]).toHaveAttribute('data-export-disabled', 'true');
    expect(menus[2]).toHaveAttribute('data-export-disabled', 'false');
    expect(menus[3]).toHaveAttribute('data-export-disabled', 'true'); // активный вариант пуст
  });

  it('экспорт отправляет текущий вариант ответа (или content) в exportMessageContent', () => {
    hx.messages = [
      msg({ id: 'a1', content: 'обычный' }),
      msg({ id: 'a2', content: 'x', alternativeResponses: ['v0', 'v1'], currentResponseIndex: 1 }),
      msg({ id: 'a3', content: 'исходный', alternativeResponses: ['v0'], currentResponseIndex: 7 }),
    ];
    const { container } = renderPage();
    const cs = cards(container);
    fireEvent.click(within(cs[0]).getByTestId('mm-export'));
    expect(hx.exports.exportMessageContent).toHaveBeenLastCalledWith('обычный', 'md', 'ответ');
    fireEvent.click(within(cs[1]).getByTestId('mm-export'));
    expect(hx.exports.exportMessageContent).toHaveBeenLastCalledWith('v1', 'md', 'ответ');
    fireEvent.click(within(cs[2]).getByTestId('mm-export'));
    expect(hx.exports.exportMessageContent).toHaveBeenLastCalledWith('исходный', 'md', 'ответ');
  });
});

describe('UnifiedChatPage: оценка ответа (feedback)', () => {
  const fbUrl = 'http://api/api/messages/c1/a1/feedback';
  const setup = (extra: Record<string, unknown> = {}) => {
    hx.messages = [msg({ id: 'a1', content: 'ответ', ...extra })];
    return renderPage();
  };

  it('лайк: PUT с токеном, обновление сообщения и благодарность', async () => {
    const fetchMock = installFetch({
      '/feedback': () =>
        fakeResponse({ feedback: { rating: 'like', tags: [1, 'x'], comment: 'c', updated_at: '2025-01-01' } }),
    });
    const { container } = setup();
    fireEvent.click(within(cards(container)[0]).getByTestId('fb-like'));
    await waitFor(() =>
      expect(hx.actions.showNotification).toHaveBeenCalledWith('success', 'Спасибо! Отметили как хороший ответ'),
    );
    const call = fetchMock.mock.calls.find((c) => c[0] === fbUrl)!;
    expect(call[1].method).toBe('PUT');
    expect(call[1].headers.Authorization).toBe('Bearer tok');
    expect(JSON.parse(call[1].body)).toEqual({ rating: 'like', tags: [], comment: '' });
    const args = hx.actions.updateMessage.mock.calls[0];
    expect(args).toHaveLength(14);
    expect(args[13]).toEqual({ rating: 'like', tags: ['1', 'x'], comment: 'c', updatedAt: '2025-01-01' });
  });

  it('дизлайк передаёт теги и комментарий и благодарит иначе', async () => {
    const fetchMock = installFetch({ '/feedback': () => fakeResponse({ feedback: { rating: 'dislike', tags: 'nope', comment: 5 } }) });
    const { container } = setup();
    fireEvent.click(within(cards(container)[0]).getByTestId('fb-dislike'));
    await waitFor(() =>
      expect(hx.actions.showNotification).toHaveBeenCalledWith(
        'success',
        'Спасибо за отзыв — учтём в следующих ответах',
      ),
    );
    const body = JSON.parse(fetchMock.mock.calls.find((c) => c[0] === fbUrl)![1].body);
    expect(body).toEqual({ rating: 'dislike', tags: ['t1'], comment: 'cm' });
    expect(hx.actions.updateMessage.mock.calls[0][13]).toEqual({
      rating: 'dislike',
      tags: [],
      comment: undefined,
      updatedAt: undefined,
    });
  });

  it('снятие оценки (rating=null) сохраняет feedback=null без благодарности', async () => {
    installFetch({ '/feedback': () => fakeResponse({ feedback: null }) });
    const { container } = setup();
    fireEvent.click(within(cards(container)[0]).getByTestId('fb-clear'));
    await waitFor(() => expect(hx.actions.updateMessage).toHaveBeenCalled());
    expect(hx.actions.updateMessage.mock.calls[0][13]).toBeNull();
    expect(hx.actions.showNotification).not.toHaveBeenCalled();
  });

  it('feedback с неизвестным рейтингом превращается в null', async () => {
    installFetch({ '/feedback': () => fakeResponse({ feedback: { rating: 'meh' } }) });
    const { container } = setup();
    fireEvent.click(within(cards(container)[0]).getByTestId('fb-like'));
    await waitFor(() => expect(hx.actions.updateMessage).toHaveBeenCalled());
    expect(hx.actions.updateMessage.mock.calls[0][13]).toBeNull();
  });

  it('ошибка сервера: берёт detail из тела, иначе стандартный текст', async () => {
    installFetch({ '/feedback': () => fakeResponse({ detail: 'Нельзя' }, { ok: false, status: 400 }) });
    const { container, unmount } = setup();
    fireEvent.click(within(cards(container)[0]).getByTestId('fb-like'));
    await waitFor(() => expect(hx.actions.showNotification).toHaveBeenCalledWith('error', 'Нельзя'));
    expect(hx.actions.updateMessage).not.toHaveBeenCalled();
    unmount();

    hx.actions.showNotification.mockClear();
    installFetch({ '/feedback': () => fakeResponse({}, { ok: false, status: 500, badJson: true }) });
    const second = setup();
    fireEvent.click(within(cards(second.container)[0]).getByTestId('fb-like'));
    await waitFor(() =>
      expect(hx.actions.showNotification).toHaveBeenCalledWith('error', 'Не удалось сохранить отзыв'),
    );
  });

  it('ошибка без поля detail оставляет стандартный текст', async () => {
    installFetch({ '/feedback': () => fakeResponse({}, { ok: false, status: 500 }) });
    const { container } = setup();
    fireEvent.click(within(cards(container)[0]).getByTestId('fb-like'));
    await waitFor(() =>
      expect(hx.actions.showNotification).toHaveBeenCalledWith('error', 'Не удалось сохранить отзыв'),
    );
  });

  it('сетевая ошибка даёт тост «Не удалось сохранить отзыв»', async () => {
    installFetch({
      '/feedback': () => {
        throw new Error('net');
      },
    });
    const { container } = setup();
    fireEvent.click(within(cards(container)[0]).getByTestId('fb-like'));
    await waitFor(() =>
      expect(hx.actions.showNotification).toHaveBeenCalledWith('error', 'Не удалось сохранить отзыв'),
    );
  });

  it('без токена просит авторизацию; токен из localStorage подхватывается', async () => {
    hx.auth.token = null;
    const fetchMock = installFetch({ '/feedback': () => fakeResponse({ feedback: null }) });
    const { container, unmount } = setup();
    fireEvent.click(within(cards(container)[0]).getByTestId('fb-like'));
    expect(hx.actions.showNotification).toHaveBeenCalledWith('error', 'Нужна авторизация, чтобы отправить отзыв');
    expect(fetchMock.mock.calls.some((c) => c[0] === fbUrl)).toBe(false);
    unmount();

    localStorage.setItem('auth_token', 'ls-token');
    const second = setup();
    fireEvent.click(within(cards(second.container)[0]).getByTestId('fb-like'));
    await waitFor(() => expect(fetchMock.mock.calls.some((c) => c[0] === fbUrl)).toBe(true));
    expect(fetchMock.mock.calls.find((c) => c[0] === fbUrl)![1].headers.Authorization).toBe('Bearer ls-token');
  });

  it('оценка без текущего чата молча игнорируется', () => {
    hx.chat = null;
    const fetchMock = installFetch();
    hx.messages = [msg({ id: 'a1', content: 'ответ' })];
    const { container } = renderPage();
    fireEvent.click(within(cards(container)[0]).getByTestId('fb-like'));
    expect(fetchMock.mock.calls.some((c) => String(c[0]).includes('feedback'))).toBe(false);
    expect(hx.actions.showNotification).not.toHaveBeenCalled();
  });
});

describe('UnifiedChatPage: ветка чата', () => {
  it('создаёт ветку и переходит на главную', async () => {
    hx.messages = [userMsg({ id: 'u1' }), msg({ id: 'a1' })];
    const { container } = renderPage();
    fireEvent.click(within(cards(container)[1]).getByTestId('mm-branch'));
    await waitFor(() => expect(hx.navigate).toHaveBeenCalledWith('/'));
    expect(hx.actions.branchChatAtMessage).toHaveBeenCalledWith('c1', 'a1', { multiLlmSlotIndex: undefined });
    expect(hx.actions.showNotification).toHaveBeenCalledWith('success', 'Создана ветка в новом чате');
  });

  it('если ветка не создана, показывает ошибку и не навигирует', async () => {
    hx.actions.branchChatAtMessage.mockResolvedValue(null);
    hx.messages = [msg({ id: 'a1' })];
    const { container } = renderPage();
    fireEvent.click(within(cards(container)[0]).getByTestId('mm-branch'));
    await waitFor(() =>
      expect(hx.actions.showNotification).toHaveBeenCalledWith('error', 'Не удалось создать ветку'),
    );
    expect(hx.navigate).not.toHaveBeenCalled();
  });

  it('для сообщения пользователя и для потокового ответа ветка не создаётся', () => {
    hx.messages = [userMsg({ id: 'u1' }), msg({ id: 'a1', isStreaming: true })];
    const { container } = renderPage();
    fireEvent.click(within(cards(container)[0]).getByTestId('mm-branch'));
    expect(hx.actions.branchChatAtMessage).not.toHaveBeenCalled();
    fireEvent.click(within(cards(container)[1]).getByTestId('mm-branch'));
    expect(hx.actions.branchChatAtMessage).not.toHaveBeenCalled();
    expect(hx.actions.showNotification).toHaveBeenCalledWith('info', 'Дождитесь окончания генерации ответа');
  });

  it('без текущего чата ничего не происходит', () => {
    hx.chat = null;
    hx.messages = [msg({ id: 'a1' })];
    const { container } = renderPage();
    fireEvent.click(within(cards(container)[0]).getByTestId('mm-branch'));
    expect(hx.actions.branchChatAtMessage).not.toHaveBeenCalled();
  });
});

describe('UnifiedChatPage: озвучивание (TTS)', () => {
  let audioInstances: any[];
  beforeEach(() => {
    audioInstances = [];
    (global as any).Audio = class {
      src: string;
      onended: (() => void) | null = null;
      onerror: (() => void) | null = null;
      pause = jest.fn();
      play = jest.fn().mockResolvedValue(undefined);
      constructor(src: string) {
        this.src = src;
        audioInstances.push(this);
      }
    };
    (URL as any).createObjectURL = jest.fn(() => 'blob:audio');
    (URL as any).revokeObjectURL = jest.fn();
  });
  afterEach(() => {
    delete (global as any).Audio;
  });

  const read = (extra: Record<string, unknown> = {}) => {
    hx.messages = [msg({ id: 'a1', content: 'озвучь', ...extra })];
    const r = renderPage();
    fireEvent.click(within(cards(r.container)[0]).getByTestId('mm-read'));
    return r;
  };

  it('отправляет текст на синтез с настройками голоса и проигрывает аудио', async () => {
    localStorage.setItem('voice_id', 'en');
    localStorage.setItem('voice_speaker', 'spk');
    localStorage.setItem('speech_rate', '1.5');
    const fetchMock = installFetch({ '/api/voice/synthesize': () => fakeResponse({}) });
    read();
    await waitFor(() => expect(audioInstances).toHaveLength(1));
    const call = fetchMock.mock.calls.find((c) => String(c[0]).includes('/voice/synthesize'))!;
    expect(JSON.parse(call[1].body)).toEqual({
      text: 'озвучь',
      voice_id: 'en',
      voice_speaker: 'spk',
      speech_rate: 1.5,
    });
    expect(audioInstances[0].play).toHaveBeenCalled();
    // окончание воспроизведения освобождает URL
    act(() => {
      audioInstances[0].onended();
    });
    expect((URL as any).revokeObjectURL).toHaveBeenCalledWith('blob:audio');
  });

  it('ошибка воспроизведения даёт тост и освобождает URL', async () => {
    installFetch({ '/api/voice/synthesize': () => fakeResponse({}) });
    read();
    await waitFor(() => expect(audioInstances).toHaveLength(1));
    act(() => {
      audioInstances[0].onerror();
    });
    expect(hx.actions.showNotification).toHaveBeenCalledWith('error', 'Ошибка воспроизведения речи');
    expect((URL as any).revokeObjectURL).toHaveBeenCalled();
  });

  it('повторное озвучивание останавливает предыдущее аудио', async () => {
    installFetch({ '/api/voice/synthesize': () => fakeResponse({}) });
    const { container } = read();
    await waitFor(() => expect(audioInstances).toHaveLength(1));
    fireEvent.click(within(cards(container)[0]).getByTestId('mm-read'));
    await waitFor(() => expect(audioInstances).toHaveLength(2));
    expect(audioInstances[0].pause).toHaveBeenCalled();
  });

  it('ответ сервера не OK или исключение fetch — тост «Ошибка синтеза речи»', async () => {
    installFetch({ '/api/voice/synthesize': () => fakeResponse({}, { ok: false, status: 500 }) });
    const { unmount } = read();
    await waitFor(() =>
      expect(hx.actions.showNotification).toHaveBeenCalledWith('error', 'Ошибка синтеза речи'),
    );
    unmount();
    hx.actions.showNotification.mockClear();
    installFetch({
      '/api/voice/synthesize': () => {
        throw new Error('net');
      },
    });
    read();
    await waitFor(() =>
      expect(hx.actions.showNotification).toHaveBeenCalledWith('error', 'Ошибка синтеза речи'),
    );
  });

  it('пустой текст не озвучивается', () => {
    const fetchMock = installFetch();
    read({ content: '   ' });
    expect(fetchMock.mock.calls.some((c) => String(c[0]).includes('voice'))).toBe(false);
  });

  it('читает активный вариант ответа, если он есть', async () => {
    const fetchMock = installFetch({ '/api/voice/synthesize': () => fakeResponse({}) });
    read({ alternativeResponses: ['первый', 'второй'], currentResponseIndex: 1 });
    await waitFor(() => expect(audioInstances).toHaveLength(1));
    const call = fetchMock.mock.calls.find((c) => String(c[0]).includes('/voice/synthesize'))!;
    expect(JSON.parse(call[1].body).text).toBe('второй');
  });

  it('индекс варианта вне диапазона — читается исходный content', async () => {
    const fetchMock = installFetch({ '/api/voice/synthesize': () => fakeResponse({}) });
    read({ alternativeResponses: ['первый'], currentResponseIndex: 5 });
    await waitFor(() => expect(audioInstances).toHaveLength(1));
    const call = fetchMock.mock.calls.find((c) => String(c[0]).includes('/voice/synthesize'))!;
    expect(JSON.parse(call[1].body).text).toBe('озвучь');
  });
});

describe('UnifiedChatPage: режим «Поделиться»', () => {
  // Продуктовое поведение memo: вход в режим предвыбирает пару сообщения, панель — «Выбрать все» /
  // «Отменить» / «Копировать ссылку» (ссылка копируется в буфер сразу, без диалога подтверждения).
  const pairs = () => {
    hx.messages = [
      userMsg({ id: 'u1' }),
      msg({ id: 'a1' }),
      userMsg({ id: 'u2' }),
      msg({ id: 'a2' }),
      userMsg({ id: 'u3' }),
    ];
  };
  beforeEach(() => {
    // jsdom не реализует scrollIntoView: страница подтягивает выбранную пару к панели кнопок
    (Element.prototype as any).scrollIntoView = jest.fn();
  });
  const enter = (container: HTMLElement) =>
    fireEvent.click(within(cards(container)[1]).getByLabelText('Поделиться'));
  const copyBtn = () => screen.getByRole('button', { name: 'Копировать ссылку' });

  it('вход в режим показывает панель, чекбоксы пар и скрывает кнопки «Поделиться»', () => {
    pairs();
    const { container } = renderPage();
    enter(container);
    expect(screen.getByLabelText('Выбрать все')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Отменить' })).toBeInTheDocument();
    // чекбокс «выбрать все» + 2 чекбокса пар (u3 без ответа — не пара)
    expect(screen.getAllByRole('checkbox')).toHaveLength(3);
    expect(within(cards(container)[1]).queryByLabelText('Поделиться')).toBeNull();
    // пара, с которой вошли, предвыбрана
    expect(copyBtn()).toBeEnabled();
  });

  it('выбор пары, «выбрать все», снятие и отмена режима', () => {
    pairs();
    const { container } = renderPage();
    enter(container);
    const all = screen.getByLabelText('Выбрать все');
    const [p1, p2] = screen.getAllByRole('checkbox').filter((c) => c !== all);
    expect(p1).toBeChecked();
    expect(p2).not.toBeChecked();
    fireEvent.click(p1); // оба выбраны -> снимаем
    expect(p1).not.toBeChecked();
    expect(copyBtn()).toBeDisabled();
    fireEvent.click(all);
    expect(all).toBeChecked();
    expect(p1).toBeChecked();
    expect(p2).toBeChecked();
    fireEvent.click(all);
    expect(all).not.toBeChecked();
    expect(copyBtn()).toBeDisabled();
    fireEvent.click(p2);
    expect(copyBtn()).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: 'Отменить' }));
    expect(screen.queryByLabelText('Выбрать все')).toBeNull();
  });

  it('создание ссылки: POST /api/share/create, копирование URL в буфер и выход из режима', async () => {
    pairs();
    localStorage.setItem('token', 'legacy-token');
    const fetchMock = installFetch({ '/api/share/create': () => fakeResponse({ share_id: 'xyz' }) });
    const { container } = renderPage();
    enter(container);
    fireEvent.click(screen.getByLabelText('Выбрать все'));
    fireEvent.click(copyBtn());
    await waitFor(() =>
      expect(clipboard.writeText).toHaveBeenCalledWith(`${window.location.origin}/share/xyz`),
    );
    expect(hx.actions.showNotification).toHaveBeenCalledWith('success', 'Ссылка скопирована');
    const call = fetchMock.mock.calls.find((c) => String(c[0]).includes('/share/create'))!;
    expect(call[1].method).toBe('POST');
    expect(call[1].headers.Authorization).toBe('Bearer stored');
    expect(JSON.parse(call[1].body).messages.map((m: any) => m.id)).toEqual(['u1', 'a1', 'u2', 'a2']);
    // после успеха режим выбора завершается
    await waitFor(() => expect(screen.queryByLabelText('Выбрать все')).toBeNull());
  });

  it('ошибка создания ссылки: тост об ошибке, буфер не трогается, режим выбора остаётся', async () => {
    pairs();
    installFetch({ '/api/share/create': () => fakeResponse({}, { ok: false, status: 500 }) });
    const { container } = renderPage();
    enter(container);
    fireEvent.click(screen.getByLabelText('Выбрать все'));
    fireEvent.click(copyBtn());
    await waitFor(() =>
      expect(hx.actions.showNotification).toHaveBeenCalledWith('error', 'Ошибка создания публичной ссылки'),
    );
    expect(clipboard.writeText).not.toHaveBeenCalled();
    expect(screen.getByLabelText('Выбрать все')).toBeInTheDocument();
  });

  it('не-Error исключение даёт общий текст «Произошла ошибка»', async () => {
    pairs();
    installFetch({
      '/api/share/create': () => {
        // eslint-disable-next-line no-throw-literal
        throw 'строка';
      },
    });
    const { container } = renderPage();
    enter(container);
    fireEvent.click(screen.getByLabelText('Выбрать все'));
    fireEvent.click(copyBtn());
    await waitFor(() =>
      expect(hx.actions.showNotification).toHaveBeenCalledWith('error', 'Произошла ошибка'),
    );
  });

  it('панель открывается при любом состоянии сайдбара (три варианта)', () => {
    pairs();
    for (const props of [{ sidebarHidden: true }, { sidebarOpen: false }, {}]) {
      const { container, unmount } = render(<UnifiedChatPage isDarkMode {...props} />);
      enter(container);
      expect(screen.getByLabelText('Выбрать все')).toBeInTheDocument();
      unmount();
    }
  });

  it('вход в режим из колонки multi-LLM тоже открывает панель', () => {
    hx.messages = [
      userMsg({ id: 'u1' }),
      msg({ id: 'a1', multiLLMResponses: [{ model: 'm1', content: 'A' }] }),
    ];
    const { container } = renderPage();
    enter(container);
    expect(screen.getByLabelText('Выбрать все')).toBeInTheDocument();
    expect(within(cards(container)[1]).queryByLabelText('Поделиться')).toBeNull();
  });
});
