import React from 'react';
import { render, screen, fireEvent, act, waitFor, within } from '@testing-library/react';
import '@testing-library/jest-dom';
import { hx, resetHarness, installFetch, fakeResponse, msg, userMsg } from '@/pages/__test_support__/unifiedChatHarness';
import UnifiedChatPage from '@/pages/UnifiedChatPage';

const MODELS = [
  { name: 'm1', path: 'p/m1', display_name: 'Модель 1', provider_id: 'prov', provider_kind: 'openai', provider_default: false },
  { name: 'm2', path: 'p/m2', provider_id: 'def', provider_default: true },
  { name: 'm3', path: '' },
  { name: 'm4', path: 'p/m4', provider_id: 'x', provider_default: false },
];

function modelsFetch(extra: Record<string, (url: string, init?: any) => any> = {}) {
  return installFetch({
    '/api/models/available': () => fakeResponse({ models: MODELS }),
    ...extra,
  });
}
async function renderLoaded() {
  const r = render(<UnifiedChatPage isDarkMode={false} />);
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
  return r;
}
const fields = () => Array.from(document.querySelectorAll('input[id^="multi-llm-model-"]')) as HTMLInputElement[];
const openField = (i = 0) => fireEvent.click(fields()[i]);
const pick = (label: string) => fireEvent.click(screen.getByText(label));
const input = () => screen.getByTestId('input-field') as HTMLInputElement;
const type = (v: string) => fireEvent.change(input(), { target: { value: v } });

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  resetHarness();
  installFetch();
  localStorage.setItem('model_comparison_enabled', 'true');
  localStorage.setItem('last_selected_model_path', 'prov/model-a');
});

afterEach(() => {
  jest.useRealTimers();
});

describe('UnifiedChatPage: режим сравнения моделей — тулбар', () => {
  it('в режиме сравнения без выбранных моделей ввод заблокирован с подсказкой', async () => {
    await renderLoaded();
    const bar = screen.getByTestId('input-bar');
    expect(bar).toHaveAttribute('data-placeholder', 'Выберите модели для сравнения (до 4, хотя бы одну)');
    for (const a of ['data-input-disabled', 'data-send-disabled', 'data-attach-disabled', 'data-settings-disabled', 'data-voice-disabled']) {
      expect(bar).toHaveAttribute(a, 'true');
    }
    expect(fields()).toHaveLength(1);
  });

  it('вне режима сравнения тулбара моделей нет', async () => {
    localStorage.setItem('model_comparison_enabled', 'false');
    await renderLoaded();
    expect(fields()).toHaveLength(0);
  });

  it('пока список моделей не загружен, поле отключено и меню не открывается', () => {
    render(<UnifiedChatPage isDarkMode={false} />);
    expect(fields()[0]).toBeDisabled();
    openField();
    expect(screen.queryByText('Не выбрано')).toBeNull();
  });

  it('меню показывает «Не выбрано», модели и подсказку про провайдера', async () => {
    modelsFetch();
    await renderLoaded();
    openField();
    expect(screen.getByText('Не выбрано')).toBeInTheDocument();
    expect(screen.getByText('Модель 1')).toBeInTheDocument();
    expect(screen.getByText('prov · openai')).toBeInTheDocument();
    expect(screen.getByText('m2')).toBeInTheDocument();
    expect(screen.getByText('m3')).toBeInTheDocument(); // без path используем name
    expect(screen.getByText('x')).toBeInTheDocument(); // провайдер без kind
    expect(screen.queryByText('def')).toBeNull(); // провайдер по умолчанию не подписывается
    // повторный клик по тому же полю закрывает меню
    openField();
    await waitFor(() => expect(screen.queryByText('Не выбрано')).toBeNull());
  });

  it('выбор модели записывает её в поле, снимает блокировку ввода; «Не выбрано» очищает', async () => {
    modelsFetch();
    await renderLoaded();
    openField();
    pick('Модель 1');
    expect(fields()[0]).toHaveValue('Модель 1');
    expect(screen.getByTestId('input-bar')).toHaveAttribute('data-input-disabled', 'false');
    expect(screen.getByTestId('ctx-usage')).toHaveAttribute('data-label', 'Модель 1');
    openField();
    pick('Не выбрано');
    expect(fields()[0]).toHaveValue('');
    expect(screen.getByTestId('input-bar')).toHaveAttribute('data-input-disabled', 'true');
  });

  it('модель без display_name показывается по name; подпись контекста для пути без записи — хвост пути', async () => {
    modelsFetch();
    await renderLoaded();
    openField();
    pick('m2');
    expect(fields()[0]).toHaveValue('m2');
    expect(hx.lastContextUsageArgs.multiLlmModelPaths).toEqual(['p/m2']);
    expect(hx.lastContextUsageArgs.isMultiLlmMode).toBe(true);
  });

  it('добавление колонок до 4, исключение уже выбранных моделей из меню, удаление колонки', async () => {
    modelsFetch();
    await renderLoaded();
    openField(0);
    pick('Модель 1');
    fireEvent.click(screen.getByLabelText('Добавить модель (максимум 4)').querySelector('button')!);
    expect(fields()).toHaveLength(2);
    openField(1);
    // модель 1 уже выбрана в первом окне — в меню второго её нет
    expect(screen.queryByText('Модель 1', { selector: 'div' })).toBeNull();
    pick('m2');
    expect(hx.lastContextUsageArgs.multiLlmModelPaths).toEqual(['p/m1', 'p/m2']);
    expect(screen.getByTestId('ctx-usage')).toHaveAttribute('data-label', '2 модели (мин. лимит)');

    fireEvent.click(screen.getByLabelText('Добавить модель (максимум 4)').querySelector('button')!);
    fireEvent.click(screen.getByLabelText('Добавить модель (максимум 4)').querySelector('button')!);
    expect(fields()).toHaveLength(4);
    expect(screen.queryByLabelText('Добавить модель (максимум 4)')).toBeNull();

    const removeButtons = screen.getAllByLabelText('Убрать колонку');
    fireEvent.click(removeButtons[3].querySelector('button')!);
    expect(fields()).toHaveLength(3);
  });

  it('удаление единственной колонки выходит из режима сравнения', async () => {
    await renderLoaded();
    fireEvent.click(screen.getByLabelText('Выйти из режима сравнения').querySelector('button')!);
    expect(localStorage.getItem('model_comparison_enabled')).toBe('false');
    expect(hx.actions.showNotification).toHaveBeenCalledWith('info', 'Режим: обычный чат');
    expect(fields()).toHaveLength(0);
  });

  it('меню закрывается, если колонку с открытым меню удалили', async () => {
    modelsFetch();
    await renderLoaded();
    fireEvent.click(screen.getByLabelText('Добавить модель (максимум 4)').querySelector('button')!);
    openField(1);
    expect(screen.getByText('Не выбрано')).toBeInTheDocument();
    // удаляем вторую колонку кнопкой рядом с ней
    fireEvent.click(screen.getAllByLabelText('Убрать колонку')[1].querySelector('button')!);
    await waitFor(() => expect(screen.queryByText('Не выбрано')).toBeNull());
  });

  it('при стриминге меню закрывается, а поля и кнопки отключены', async () => {
    modelsFetch();
    hx.messages = [userMsg({ id: 'u1' }), msg({ id: 'a1', content: 'x' })];
    const { rerender } = await renderLoaded();
    openField();
    expect(screen.getByText('Не выбрано')).toBeInTheDocument();
    hx.messages = [userMsg({ id: 'u1' }), msg({ id: 'a1', content: 'x', isStreaming: true })];
    rerender(<UnifiedChatPage isDarkMode={false} />);
    await waitFor(() => expect(screen.queryByText('Не выбрано')).toBeNull());
    expect(fields()[0]).toBeDisabled();
    openField();
    expect(screen.queryByText('Не выбрано')).toBeNull();
  });

  it('клик по фону закрывает меню', async () => {
    modelsFetch();
    await renderLoaded();
    openField();
    fireEvent.keyDown(screen.getByText('Не выбрано'), { key: 'Escape' });
    await waitFor(() => expect(screen.queryByText('Не выбрано')).toBeNull());
  });

  it('подпись контекста вне режима сравнения берётся из last_selected_model_path', async () => {
    localStorage.setItem('model_comparison_enabled', 'false');
    modelsFetch();
    localStorage.setItem('last_selected_model_path', 'p/m1');
    await renderLoaded();
    expect(screen.getByTestId('ctx-usage')).toHaveAttribute('data-label', 'Модель 1');
  });

  it('если модели нет в списке — подпись это хвост пути; без выбора подписи нет', async () => {
    localStorage.setItem('model_comparison_enabled', 'false');
    await renderLoaded();
    expect(screen.getByTestId('ctx-usage')).toHaveAttribute('data-label', 'model-a');
  });

  it('без сохранённого пути подпись пуста', async () => {
    localStorage.setItem('model_comparison_enabled', 'false');
    localStorage.removeItem('last_selected_model_path');
    await renderLoaded();
    expect(screen.getByTestId('ctx-usage')).toHaveAttribute('data-label', '');
  });

  it('один выбранный путь в режиме сравнения без записи в списке отображается как есть', async () => {
    modelsFetch();
    await renderLoaded();
    openField();
    pick('m3');
    expect(screen.getByTestId('ctx-usage')).toHaveAttribute('data-label', 'm3');
  });

  it('загрузка моделей: повторные одинаковые данные не ломают, ошибки сети игнорируются', async () => {
    installFetch({
      '/api/models/available': () => {
        throw new Error('net');
      },
    });
    await renderLoaded();
    expect(fields()[0]).toBeDisabled();
    installFetch({ '/api/models/available': () => fakeResponse({}, { ok: false, status: 500 }) });
    await renderLoaded();
    installFetch({ '/api/models/available': () => fakeResponse({}) });
    await renderLoaded();
    expect(screen.getAllByTestId('input-bar').length).toBeGreaterThan(0);
  });
});

describe('UnifiedChatPage: переключение режима сравнения', () => {
  it('кнопка в заголовке включает и выключает режим, сбрасывая выбранные модели', async () => {
    localStorage.setItem('model_comparison_enabled', 'false');
    modelsFetch();
    await renderLoaded();
    const toggle = () => screen.getByLabelText('Сравнение моделей').querySelector('button') as HTMLElement;
    fireEvent.click(toggle());
    expect(localStorage.getItem('model_comparison_enabled')).toBe('true');
    expect(hx.actions.showNotification).toHaveBeenCalledWith('info', 'Режим: сравнение моделей');
    openField();
    pick('Модель 1');
    fireEvent.click(toggle());
    expect(localStorage.getItem('model_comparison_enabled')).toBe('false');
    fireEvent.click(toggle());
    // после повторного включения выбор модели сброшен
    expect(fields()[0]).toHaveValue('');
  });

  it('кнопка отключена, пока идёт генерация', async () => {
    localStorage.setItem('model_comparison_enabled', 'false');
    hx.state.loadingChatIds = ['c1'];
    await renderLoaded();
    expect(screen.getByLabelText('Сравнение моделей').querySelector('button')).toBeDisabled();
  });
});

describe('UnifiedChatPage: отправка в режиме сравнения', () => {
  async function ready(models = ['Модель 1']) {
    const fetchMock = modelsFetch({
      '/api/model-comparison/models': () => fakeResponse({}),
    });
    const r = await renderLoaded();
    for (let i = 0; i < models.length; i++) {
      if (i > 0) {
        fireEvent.click(screen.getByLabelText('Добавить модель (максимум 4)').querySelector('button')!);
      }
      openField(i);
      pick(models[i]);
    }
    return { fetchMock, ...r };
  }
  const postCalls = (f: jest.Mock) => f.mock.calls.filter((c) => String(c[0]).includes('/model-comparison/models'));

  it('устанавливает модели на сервере и отправляет сообщение с expectMultiLlm', async () => {
    jest.useFakeTimers();
    const { fetchMock } = await ready(['Модель 1', 'm2']);
    type('сравни');
    await act(async () => {
      fireEvent.click(screen.getByTestId('ib-send'));
    });
    expect(postCalls(fetchMock)).toHaveLength(1);
    expect(JSON.parse(postCalls(fetchMock)[0][1].body)).toEqual({ models: ['p/m1', 'p/m2'] });
    expect(hx.actions.setLoading).toHaveBeenCalledWith(true);
    expect(hx.socket.sendMessage).toHaveBeenCalledWith('сравни', 'c1', true, undefined, true, undefined);
    expect(input()).toHaveValue('');
    input().blur();
    act(() => {
      jest.advanceTimersByTime(20);
    });
    expect(input()).toHaveFocus();

    // повторная отправка с теми же моделями не ходит на сервер
    type('ещё');
    await act(async () => {
      fireEvent.click(screen.getByTestId('ib-send'));
    });
    expect(postCalls(fetchMock)).toHaveLength(1);
    expect(hx.socket.sendMessage).toHaveBeenCalledTimes(2);
  });

  it('после выхода и повторного входа в режим набор моделей отправляется заново', async () => {
    const { fetchMock } = await ready();
    type('раз');
    await act(async () => {
      fireEvent.click(screen.getByTestId('ib-send'));
    });
    expect(postCalls(fetchMock)).toHaveLength(1);
    fireEvent.click(screen.getByLabelText('Выйти из режима сравнения').querySelector('button')!);
  });

  it('сервер отказал в установке моделей: setLoading(false) и тост ошибки', async () => {
    modelsFetch({ '/api/model-comparison/models': () => fakeResponse({}, { ok: false, status: 500 }) });
    await renderLoaded();
    openField();
    pick('Модель 1');
    type('сравни');
    await act(async () => {
      fireEvent.click(screen.getByTestId('ib-send'));
    });
    expect(hx.actions.setLoading).toHaveBeenLastCalledWith(false);
    expect(hx.actions.showNotification).toHaveBeenCalledWith('error', 'Ошибка отправки сообщения');
    expect(hx.socket.sendMessage).not.toHaveBeenCalled();
  });

  it('пустой ввод и блокировка генерацией ничего не отправляют', async () => {
    await ready();
    await act(async () => {
      fireEvent.click(screen.getByTestId('ib-send'));
    });
    expect(hx.socket.sendMessage).not.toHaveBeenCalled();

    hx.state.loadingChatIds = ['c1'];
    type('что-то');
    await act(async () => {
      fireEvent.click(screen.getByTestId('ib-send'));
    });
    expect(hx.socket.sendMessage).not.toHaveBeenCalled();
  });

  it('без соединения и без токена отправка молча отменяется', async () => {
    hx.socket.isConnected = false;
    hx.socket.isConnecting = false;
    hx.auth.token = null;
    await renderLoaded();
    type('привет');
    await act(async () => {
      fireEvent.click(screen.getByTestId('ib-send'));
    });
    expect(hx.socket.sendMessage).not.toHaveBeenCalled();
  });

  it('если модель не выбрана, показывает баннер «Модель не выбрана»', async () => {
    await renderLoaded();
    type('привет');
    await act(async () => {
      fireEvent.click(screen.getByTestId('ib-send'));
    });
    expect(screen.getByTestId('top-banner-msg')).toHaveTextContent('Модель не выбрана! Пожалуйста, выберите модель');
    expect(hx.socket.sendMessage).not.toHaveBeenCalled();
  });

  it('если чата ещё нет, создаёт его и отправляет туда', async () => {
    hx.chat = null;
    modelsFetch({ '/api/model-comparison/models': () => fakeResponse({}) });
    await renderLoaded();
    openField();
    pick('Модель 1');
    type('привет');
    await act(async () => {
      fireEvent.click(screen.getByTestId('ib-send'));
    });
    expect(hx.actions.createChat).toHaveBeenCalledWith('Новый чат');
    expect(hx.actions.setCurrentChat).toHaveBeenCalledWith('new-chat');
    expect(hx.socket.sendMessage).toHaveBeenCalledWith('привет', 'new-chat', true, undefined, true, undefined);
  });

  it('вложения передаются в payload сравнения', async () => {
    modelsFetch({
      '/api/model-comparison/models': () => fakeResponse({}),
      '/documents/attach': () =>
        fakeResponse({ type: 'image', content: 'IMG', filename: 'i.png', minio_object: 'o', minio_bucket: 'b' }),
    });
    (URL as any).createObjectURL = jest.fn(() => 'blob:x');
    (URL as any).revokeObjectURL = jest.fn();
    await renderLoaded();
    openField();
    pick('Модель 1');
    fireEvent.change(screen.getByTestId('input-file'), {
      target: { files: [new File(['x'], 'i.png', { type: 'image/png' })] },
    });
    await screen.findByTestId('ib-remove-0');
    type('смотри');
    await act(async () => {
      fireEvent.click(screen.getByTestId('ib-send'));
    });
    const payload = hx.socket.sendMessage.mock.calls[0][5];
    expect(payload.inline_images).toEqual(['IMG']);
    expect(payload.attachments_meta[0]).toMatchObject({ name: 'i.png', minio_object: 'o', minio_bucket: 'b' });
    expect(screen.queryByTestId('ib-remove-0')).toBeNull();
  });

  it('смена чата сохраняет выбранные модели только если чат создан самой отправкой', async () => {
    modelsFetch();
    const { rerender } = await renderLoaded();
    openField();
    pick('Модель 1');
    hx.state = { ...hx.state, currentChatId: 'c2' };
    rerender(<UnifiedChatPage isDarkMode={false} />);
    expect(fields()[0]).toHaveValue('Модель 1');
  });
});
