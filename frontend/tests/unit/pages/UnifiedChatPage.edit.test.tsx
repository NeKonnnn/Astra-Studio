import React from 'react';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import '@testing-library/jest-dom';
import { hx, resetHarness, installFetch, fakeResponse, msg, userMsg } from '@/pages/__test_support__/unifiedChatHarness';
import UnifiedChatPage from '@/pages/UnifiedChatPage';

function renderPage() {
  return render(<UnifiedChatPage isDarkMode={false} />);
}
const cards = (c: HTMLElement) => Array.from(c.querySelectorAll('[data-message-index]')) as HTMLElement[];
const dialog = () => screen.getByRole('dialog');
const textarea = () => within(dialog()).getByLabelText('Текст сообщения') as HTMLTextAreaElement;
const putCalls = (f: jest.Mock) => f.mock.calls.filter((c) => c[1]?.method === 'PUT');
const UPDATE_URL = 'http://api/api/messages/c1/u1';
const WARN_LOCAL = 'Сообщение обновлено локально, но не сохранено в базе данных';

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  resetHarness();
  installFetch();
});

describe('UnifiedChatPage: диалог редактирования', () => {
  it('для сообщения пользователя показывает три кнопки и текст сообщения', () => {
    hx.messages = [userMsg({ id: 'u1', content: 'старый вопрос' })];
    const { container } = renderPage();
    expect(screen.queryByRole('dialog')).toBeNull();
    fireEvent.click(within(cards(container)[0]).getByTestId('mm-edit'));
    expect(within(dialog()).getByText('Редактировать сообщение')).toBeInTheDocument();
    expect(textarea()).toHaveValue('старый вопрос');
    expect(within(dialog()).getByRole('button', { name: 'Отменить' })).toBeInTheDocument();
    expect(within(dialog()).getByRole('button', { name: 'Сохранить' })).toBeInTheDocument();
    expect(within(dialog()).getByRole('button', { name: 'Сохранить и отправить' })).toBeInTheDocument();
  });

  it('для сообщения ассистента есть только «Сохранить»', () => {
    hx.messages = [msg({ id: 'a1', content: 'ответ' })];
    const { container } = renderPage();
    fireEvent.click(within(cards(container)[0]).getByTestId('mm-edit'));
    expect(within(dialog()).queryByRole('button', { name: 'Сохранить и отправить' })).toBeNull();
    expect(within(dialog()).getAllByRole('button', { name: 'Сохранить' })).toHaveLength(1);
  });

  it('«Отменить» закрывает диалог без сохранения', async () => {
    hx.messages = [userMsg({ id: 'u1', content: 'q' })];
    const { container } = renderPage();
    fireEvent.click(within(cards(container)[0]).getByTestId('mm-edit'));
    fireEvent.click(within(dialog()).getByRole('button', { name: 'Отменить' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(hx.actions.updateMessage).not.toHaveBeenCalled();
  });

  it('сохранение: локальное обновление, PUT с old_content и успех', async () => {
    const fetchMock = installFetch({ '/api/messages/c1/u1': () => fakeResponse({}) });
    hx.messages = [userMsg({ id: 'u1', content: 'старый' })];
    const { container } = renderPage();
    fireEvent.click(within(cards(container)[0]).getByTestId('mm-edit'));
    fireEvent.change(textarea(), { target: { value: '  новый текст  ' } });
    fireEvent.click(within(dialog()).getByRole('button', { name: 'Сохранить' }));
    await waitFor(() =>
      expect(hx.actions.showNotification).toHaveBeenCalledWith(
        'success',
        'Сообщение обновлено и сохранено в базе данных',
      ),
    );
    expect(hx.actions.updateMessage).toHaveBeenCalledWith('c1', 'u1', 'новый текст');
    const put = putCalls(fetchMock)[0];
    expect(put[0]).toBe(UPDATE_URL);
    expect(JSON.parse(put[1].body)).toEqual({ content: 'новый текст', old_content: 'старый' });
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('пустой текст не сохраняется, диалог остаётся открытым', () => {
    const fetchMock = installFetch();
    hx.messages = [userMsg({ id: 'u1', content: 'q' })];
    const { container } = renderPage();
    fireEvent.click(within(cards(container)[0]).getByTestId('mm-edit'));
    fireEvent.change(textarea(), { target: { value: '   ' } });
    fireEvent.click(within(dialog()).getByRole('button', { name: 'Сохранить' }));
    expect(hx.actions.updateMessage).not.toHaveBeenCalled();
    expect(putCalls(fetchMock)).toHaveLength(0);
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });

  it('ошибка сервера при сохранении (с телом и без) приводит к предупреждению о локальном сохранении', async () => {
    installFetch({ '/api/messages/c1/u1': () => fakeResponse({ detail: 'нет' }, { ok: false, status: 500 }) });
    hx.messages = [userMsg({ id: 'u1', content: 'q' })];
    const { container, unmount } = renderPage();
    fireEvent.click(within(cards(container)[0]).getByTestId('mm-edit'));
    fireEvent.click(within(dialog()).getByRole('button', { name: 'Сохранить' }));
    await waitFor(() => expect(hx.actions.showNotification).toHaveBeenCalledWith('warning', WARN_LOCAL));
    unmount();

    hx.actions.showNotification.mockClear();
    installFetch({ '/api/messages/c1/u1': () => fakeResponse({}, { ok: false, status: 500, badJson: true }) });
    const second = renderPage();
    fireEvent.click(within(cards(second.container)[0]).getByTestId('mm-edit'));
    fireEvent.click(within(dialog()).getByRole('button', { name: 'Сохранить' }));
    await waitFor(() => expect(hx.actions.showNotification).toHaveBeenCalledWith('warning', WARN_LOCAL));
  });

  it('сетевой сбой при сохранении тоже даёт предупреждение', async () => {
    installFetch({
      '/api/messages/c1/u1': () => {
        throw new Error('net');
      },
    });
    hx.messages = [userMsg({ id: 'u1', content: 'q' })];
    const { container } = renderPage();
    fireEvent.click(within(cards(container)[0]).getByTestId('mm-edit'));
    fireEvent.click(within(dialog()).getByRole('button', { name: 'Сохранить' }));
    await waitFor(() => expect(hx.actions.showNotification).toHaveBeenCalledWith('warning', WARN_LOCAL));
  });

  it('редактирование колонки multi-LLM: заголовок с моделью и сохранение без PUT', async () => {
    const fetchMock = installFetch();
    hx.messages = [
      userMsg({ id: 'u1' }),
      msg({
        id: 'a1',
        content: '',
        multiLLMResponses: [
          { model: 'm1', content: 'A', alternativeResponses: ['A', 'A2'], currentResponseIndex: 1 },
          { model: 'm2', content: 'B' },
        ],
      }),
    ];
    const { container } = renderPage();
    fireEvent.click(within(cards(container)[1]).getAllByTestId('mm-edit')[0]);
    expect(within(dialog()).getByText('Редактировать ответ (m1)')).toBeInTheDocument();
    expect(textarea()).toHaveValue('A2');
    expect(within(dialog()).queryByRole('button', { name: 'Сохранить и отправить' })).toBeNull();
    fireEvent.change(textarea(), { target: { value: ' исправлено ' } });
    fireEvent.click(within(dialog()).getByRole('button', { name: 'Сохранить' }));
    const args = hx.actions.updateMessage.mock.calls[0];
    expect(args.slice(0, 4)).toEqual(['c1', 'a1', undefined, false]);
    expect(args[4][0]).toEqual({
      model: 'm1',
      content: 'исправлено',
      alternativeResponses: undefined,
      currentResponseIndex: undefined,
    });
    expect(args[4][1]).toEqual({ model: 'm2', content: 'B' });
    expect(hx.actions.showNotification).toHaveBeenCalledWith('success', 'Ответ модели обновлён');
    expect(putCalls(fetchMock)).toHaveLength(0);
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('отмена редактирования колонки сбрасывает слот и текст', async () => {
    hx.messages = [msg({ id: 'a1', content: '', multiLLMResponses: [{ model: 'm1', content: 'A' }] })];
    const { container } = renderPage();
    fireEvent.click(within(cards(container)[0]).getByTestId('mm-edit'));
    fireEvent.click(within(dialog()).getByRole('button', { name: 'Отменить' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    // повторное открытие обычного сообщения снова показывает обычный заголовок
    hx.messages = [userMsg({ id: 'u1', content: 'q' })];
  });
});

describe('UnifiedChatPage: «Сохранить и отправить» и перегенерация', () => {
  const edit = (container: HTMLElement, idx: number, value: string) => {
    fireEvent.click(within(cards(container)[idx]).getByTestId('mm-edit'));
    fireEvent.change(textarea(), { target: { value } });
    fireEvent.click(within(dialog()).getByRole('button', { name: 'Сохранить и отправить' }));
  };

  it('обновляет сообщение и перегенерирует следующий ответ с новым текстом', async () => {
    const fetchMock = installFetch({ '/api/messages/c1/u1': () => fakeResponse({}) });
    hx.messages = [userMsg({ id: 'u1', content: 'старый' }), msg({ id: 'a1', content: 'ответ 1' })];
    const { container } = renderPage();
    edit(container, 0, 'новый вопрос');
    await waitFor(() =>
      expect(hx.actions.showNotification).toHaveBeenCalledWith(
        'success',
        'Сообщение обновлено и отправлено на перегенерацию',
      ),
    );
    expect(putCalls(fetchMock)).toHaveLength(1);
    expect(hx.actions.updateMessage).toHaveBeenNthCalledWith(1, 'c1', 'u1', 'новый вопрос');
    expect(hx.actions.updateMessage).toHaveBeenNthCalledWith(2, 'c1', 'a1', '', true, undefined, ['ответ 1', ''], 1);
    expect(hx.socket.regenerateResponse).toHaveBeenCalledWith('новый вопрос', 'a1', 'c1', ['ответ 1', ''], 1);
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('ошибка сохранения в БД не мешает перегенерации, но даёт предупреждение', async () => {
    installFetch({ '/api/messages/c1/u1': () => fakeResponse({}, { ok: false, status: 500, badJson: true }) });
    hx.messages = [userMsg({ id: 'u1', content: 'старый' }), msg({ id: 'a1', content: 'ответ 1' })];
    const { container } = renderPage();
    edit(container, 0, 'новый');
    await waitFor(() => expect(hx.socket.regenerateResponse).toHaveBeenCalled());
    expect(hx.actions.showNotification).toHaveBeenCalledWith('warning', WARN_LOCAL);
  });

  it('если после пользователя нет ответа ассистента, только закрывает диалог', async () => {
    hx.messages = [userMsg({ id: 'u1', content: 'старый' })];
    const { container } = renderPage();
    edit(container, 0, 'новый');
    await waitFor(() =>
      expect(hx.actions.showNotification).toHaveBeenCalledWith(
        'success',
        'Сообщение обновлено и отправлено на перегенерацию',
      ),
    );
    expect(hx.socket.regenerateResponse).not.toHaveBeenCalled();
  });

  it('без соединения показывает ошибку и ничего не меняет', async () => {
    hx.socket.isConnected = false;
    hx.socket.isConnecting = false;
    hx.messages = [userMsg({ id: 'u1', content: 'старый' }), msg({ id: 'a1' })];
    const { container } = renderPage();
    edit(container, 0, 'новый');
    expect(hx.actions.showNotification).toHaveBeenCalledWith('error', 'Нет соединения с сервером');
    expect(hx.actions.updateMessage).not.toHaveBeenCalled();
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });

  it('пустой текст при «Сохранить и отправить» (есть соединение) ничего не делает', () => {
    hx.messages = [userMsg({ id: 'u1', content: 'старый' }), msg({ id: 'a1' })];
    const { container } = renderPage();
    edit(container, 0, '  ');
    expect(hx.actions.updateMessage).not.toHaveBeenCalled();
    expect(hx.actions.showNotification).not.toHaveBeenCalled();
  });

  it('если следующий ответ — multi-LLM, подсказывает перегенерировать конкретную колонку', async () => {
    hx.messages = [
      userMsg({ id: 'u1', content: 'старый' }),
      msg({ id: 'a1', content: '', multiLLMResponses: [{ model: 'm1', content: 'A' }] }),
    ];
    const { container } = renderPage();
    edit(container, 0, 'новый');
    await waitFor(() =>
      expect(hx.actions.showNotification).toHaveBeenCalledWith(
        'info',
        'Используйте «Перегенерировать» под нужной моделью',
      ),
    );
    expect(hx.socket.regenerateResponse).not.toHaveBeenCalled();
  });
});

describe('UnifiedChatPage: кнопка «Перегенерировать» у ответа', () => {
  const regenBtn = (card: HTMLElement) => within(card).getByLabelText('Перегенерировать');

  it('сохраняет текущий ответ как вариант и запрашивает новый', () => {
    hx.messages = [userMsg({ id: 'u1', content: 'вопрос' }), msg({ id: 'a1', content: 'ответ' })];
    const { container } = renderPage();
    fireEvent.click(regenBtn(cards(container)[1]));
    expect(hx.actions.updateMessage).toHaveBeenCalledWith('c1', 'a1', '', true, undefined, ['ответ', ''], 1);
    expect(hx.socket.regenerateResponse).toHaveBeenCalledWith('вопрос', 'a1', 'c1', ['ответ', ''], 1);
  });

  it('при существующих вариантах обновляет текущий и добавляет пустой', () => {
    hx.messages = [
      userMsg({ id: 'u1', content: 'вопрос' }),
      msg({ id: 'a1', content: 'правка', alternativeResponses: ['v0', 'v1'], currentResponseIndex: 1 }),
    ];
    const { container } = renderPage();
    fireEvent.click(regenBtn(cards(container)[1]));
    expect(hx.socket.regenerateResponse).toHaveBeenCalledWith('вопрос', 'a1', 'c1', ['v0', 'правка', ''], 2);
  });

  it('индекс текущего варианта за пределами массива — текущий ответ добавляется в конец', () => {
    hx.messages = [
      userMsg({ id: 'u1', content: 'вопрос' }),
      msg({ id: 'a1', content: 'новый', alternativeResponses: ['v0'], currentResponseIndex: 5 }),
    ];
    const { container } = renderPage();
    fireEvent.click(regenBtn(cards(container)[1]));
    expect(hx.socket.regenerateResponse).toHaveBeenCalledWith('вопрос', 'a1', 'c1', ['v0', 'новый', ''], 2);
  });

  it('использует ближайшее предыдущее сообщение пользователя', () => {
    hx.messages = [
      userMsg({ id: 'u1', content: 'первый' }),
      msg({ id: 'a1', content: 'ответ 1' }),
      userMsg({ id: 'u2', content: 'второй' }),
      msg({ id: 'a2', content: 'ответ 2' }),
    ];
    const { container } = renderPage();
    fireEvent.click(regenBtn(cards(container)[3]));
    expect(hx.socket.regenerateResponse.mock.calls[0][0]).toBe('второй');
  });

  it('без предыдущего пользовательского сообщения — ошибка', () => {
    hx.messages = [msg({ id: 'a1', content: 'ответ' })];
    const { container } = renderPage();
    fireEvent.click(regenBtn(cards(container)[0]));
    expect(hx.actions.showNotification).toHaveBeenCalledWith('error', 'Не найдено предыдущее сообщение пользователя');
    expect(hx.socket.regenerateResponse).not.toHaveBeenCalled();
  });

  it('без соединения — ошибка и без запроса', () => {
    hx.socket.isConnected = false;
    hx.socket.isConnecting = false;
    hx.messages = [userMsg({ id: 'u1' }), msg({ id: 'a1' })];
    const { container } = renderPage();
    fireEvent.click(regenBtn(cards(container)[1]));
    expect(hx.actions.showNotification).toHaveBeenCalledWith('error', 'Нет соединения с сервером');
    expect(hx.socket.regenerateResponse).not.toHaveBeenCalled();
  });

  it('при установке соединения (isConnecting) перегенерация разрешена', () => {
    hx.socket.isConnected = false;
    hx.socket.isConnecting = true;
    hx.messages = [userMsg({ id: 'u1', content: 'q' }), msg({ id: 'a1', content: 'a' })];
    const { container } = renderPage();
    fireEvent.click(regenBtn(cards(container)[1]));
    expect(hx.socket.regenerateResponse).toHaveBeenCalled();
  });
});
