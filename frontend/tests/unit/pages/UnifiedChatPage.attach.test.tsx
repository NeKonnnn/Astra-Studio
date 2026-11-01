import React from 'react';
import { render, screen, fireEvent, act, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';
import { hx, resetHarness, installFetch, fakeResponse, msg, userMsg } from '@/pages/__test_support__/unifiedChatHarness';
import UnifiedChatPage from '@/pages/UnifiedChatPage';

function renderPage() {
  return render(<UnifiedChatPage isDarkMode={false} />);
}
const input = () => screen.getByTestId('input-field') as HTMLInputElement;
const OVERSIZED = 'Файл не должен превышать 20 МБ! Рекомендуем поместить его в агентный или проектный РАГ';

function attach(file: File) {
  fireEvent.change(screen.getByTestId('input-file'), { target: { files: [file] } });
}
const txt = (name = 'a.txt', body = 'abc') => new File([body], name, { type: 'text/plain' });
const bigFile = () => {
  const f = new File(['x'], 'big.pdf', { type: 'application/pdf' });
  Object.defineProperty(f, 'size', { value: 21 * 1024 * 1024 });
  return f;
};
const uploadCall = (fetchMock: jest.Mock) =>
  fetchMock.mock.calls.find((c) => String(c[0]).includes('/documents/attach'));

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  resetHarness();
  installFetch();
  localStorage.setItem('last_selected_model_path', 'prov/model-a');
  (URL as any).createObjectURL = jest.fn(() => 'blob:preview');
  (URL as any).revokeObjectURL = jest.fn();
});

afterEach(() => {
  jest.useRealTimers();
});

describe('UnifiedChatPage: прикрепление файлов — успех', () => {
  it('текстовый файл: загрузка с авторизацией, чип вложения, тосты и отправка с inline_context', async () => {
    const fetchMock = installFetch({
      '/documents/attach': () =>
        fakeResponse({
          type: 'text',
          content: 'hello world',
          filename: 'server.txt',
          minio_object: 'obj',
          minio_bucket: 'bkt',
          warning: 'Файл усечён',
        }),
    });
    renderPage();
    attach(txt('a.txt', 'abc'));
    await screen.findByTestId('ib-remove-0');
    expect(screen.getByTestId('ib-remove-0')).toHaveTextContent('server.txt');
    const call = uploadCall(fetchMock)!;
    expect(call[1].method).toBe('POST');
    expect(call[1].headers).toEqual({ Authorization: 'Bearer stored' });
    expect(call[1].body.get('file').name).toBe('a.txt');
    expect(hx.actions.showNotification).toHaveBeenCalledWith('warning', 'Файл усечён');
    expect(hx.actions.showNotification).toHaveBeenCalledWith('success', '"a.txt" прикреплён');
    expect(screen.getByTestId('input-bar')).toHaveAttribute('data-uploading', 'false');
    expect(hx.misc.logChatAttach).toHaveBeenCalledWith('attach-start', expect.objectContaining({ name: 'a.txt' }));

    // отправка вместе с вложением (без текста)
    fireEvent.click(screen.getByTestId('ib-send'));
    expect(hx.socket.sendMessage).toHaveBeenCalledWith('', 'c1', true, undefined, undefined, {
      inline_context: '[server.txt]\nhello world',
      inline_images: [],
      attachments_meta: [
        {
          name: 'server.txt',
          contentType: 'text',
          preview: undefined,
          minio_object: 'obj',
          minio_bucket: 'bkt',
          size: 3,
          tokenEstimate: expect.any(Number),
        },
      ],
    });
    expect(screen.queryByTestId('ib-remove-0')).toBeNull();
  });

  it('имя файла берётся с клиента, если сервер не вернул filename; пустой content оценивается нулём токенов', async () => {
    installFetch({ '/documents/attach': () => fakeResponse({ type: 'text', content: '' }) });
    renderPage();
    attach(txt('client.txt', ''));
    await screen.findByTestId('ib-remove-0');
    expect(screen.getByTestId('ib-remove-0')).toHaveTextContent('client.txt');
    fireEvent.click(screen.getByTestId('ib-remove-0'));
    expect(screen.queryByTestId('ib-remove-0')).toBeNull();
  });

  it('изображение: подготовка/сжатие, превью-URL освобождается, в отправку идут inline_images', async () => {
    hx.inlineImage.prepareInlineImageFile.mockResolvedValue({
      dataUrl: 'data:image/png;base64,ZZ',
      filename: 'small.png',
      wasCompressed: true,
      originalSize: 100,
      compressedSize: 50,
    });
    const fetchMock = installFetch({
      '/documents/attach': () =>
        fakeResponse({ type: 'image', content: 'data:image/png;base64,SRV', filename: 'p.png' }),
    });
    renderPage();
    const img = new File(['\x89PNG'], 'p.png', { type: 'image/png' });
    attach(img);
    await screen.findByTestId('ib-remove-0');
    expect(hx.inlineImage.prepareInlineImageFile).toHaveBeenCalledWith(img);
    expect((URL as any).createObjectURL).toHaveBeenCalledWith(img);
    expect((URL as any).revokeObjectURL).toHaveBeenCalledWith('blob:preview');
    // загружается подготовленный (сжатый) файл
    expect(uploadCall(fetchMock)![1].body.get('file').name).toBe('small.png');
    expect(hx.actions.showNotification).toHaveBeenCalledWith('success', '"p.png" прикреплён (сжато: 100B → 50B)');

    type('смотри');
    fireEvent.click(screen.getByTestId('ib-send'));
    const payload = hx.socket.sendMessage.mock.calls[0][5];
    expect(payload.inline_context).toBeUndefined();
    expect(payload.inline_images).toEqual(['data:image/png;base64,SRV']);
    expect(payload.attachments_meta[0]).toMatchObject({
      name: 'p.png',
      contentType: 'image',
      preview: 'data:image/png;base64,SRV',
      tokenEstimate: 512,
    });
  });

  it('изображение определяется и по расширению при пустом MIME', async () => {
    installFetch({ '/documents/attach': () => fakeResponse({ type: 'image', content: 'c', filename: 'x.JPG' }) });
    renderPage();
    attach(new File(['x'], 'x.JPG', { type: '' }));
    await screen.findByTestId('ib-remove-0');
    expect(hx.inlineImage.prepareInlineImageFile).toHaveBeenCalled();
  });

  it('отправка вложений в новом чате (чата ещё нет) включает payload в отложенную отправку', async () => {
    jest.useFakeTimers();
    hx.chat = null;
    installFetch({
      '/documents/attach': () => fakeResponse({ type: 'text', content: 'T', filename: 'n.txt' }),
    });
    renderPage();
    attach(txt('n.txt', 'ab'));
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });
    fireEvent.click(screen.getByTestId('ib-send'));
    act(() => {
      jest.advanceTimersByTime(60);
    });
    const payload = hx.socket.sendMessage.mock.calls[0][5];
    expect(hx.socket.sendMessage.mock.calls[0].slice(0, 5)).toEqual(['', 'new-chat', true, undefined, undefined]);
    expect(payload.inline_context).toBe('[n.txt]\nT');
  });

  it('размер 0 и отсутствие токен-оценки не попадают в attachments_meta', async () => {
    installFetch({
      '/documents/attach': () => fakeResponse({ type: 'text', content: '', filename: 'e.txt' }),
    });
    renderPage();
    attach(txt('e.txt', ''));
    await screen.findByTestId('ib-remove-0');
    fireEvent.click(screen.getByTestId('ib-send'));
    const meta = hx.socket.sendMessage.mock.calls[0][5].attachments_meta[0];
    expect(meta).not.toHaveProperty('size');
    expect(meta).not.toHaveProperty('tokenEstimate');
  });
});

describe('UnifiedChatPage: прикрепление файлов — ошибки', () => {
  it('файл больше лимита отклоняется без запроса', () => {
    const fetchMock = installFetch();
    renderPage();
    attach(bigFile());
    expect(screen.getByTestId('top-banner-msg')).toHaveTextContent(OVERSIZED);
    expect(uploadCall(fetchMock)).toBeUndefined();
    fireEvent.click(screen.getByTestId('top-banner-close'));
    expect(screen.queryByTestId('top-banner')).toBeNull();
  });

  it('HTTP 413 и 400 с сообщением о размере дают баннер про 20 МБ', async () => {
    installFetch({ '/documents/attach': () => fakeResponse({}, { ok: false, status: 413, text: '' }) });
    const { unmount } = renderPage();
    attach(txt());
    await waitFor(() => expect(screen.getByTestId('top-banner-msg')).toHaveTextContent(OVERSIZED));
    unmount();

    installFetch({
      '/documents/attach': () =>
        fakeResponse({}, { ok: false, status: 400, text: JSON.stringify({ detail: 'размер файла слишком большой' }) }),
    });
    renderPage();
    attach(txt());
    await waitFor(() => expect(screen.getByTestId('top-banner-msg')).toHaveTextContent(OVERSIZED));
  });

  it('HTTP 400 про неподдерживаемый формат даёт баннер с перечнем допустимых типов', async () => {
    installFetch({
      '/documents/attach': () =>
        fakeResponse({}, { ok: false, status: 400, text: JSON.stringify({ detail: 'Формат не поддерживается' }) }),
    });
    renderPage();
    attach(txt('weird.xyz'));
    await waitFor(() =>
      expect(screen.getByTestId('top-banner-msg').textContent).toContain('Данный тип файлов .xyz не поддерживается'),
    );
  });

  it('прочие ошибки: detail из JSON, сырой текст ответа, пустой ответ и неудачное чтение тела', async () => {
    installFetch({
      '/documents/attach': () => fakeResponse({}, { ok: false, status: 500, text: JSON.stringify({ detail: 'Упало' }) }),
    });
    const a = renderPage();
    attach(txt());
    await waitFor(() => expect(hx.actions.showNotification).toHaveBeenCalledWith('error', 'Упало'));
    a.unmount();

    hx.actions.showNotification.mockClear();
    installFetch({
      '/documents/attach': () => fakeResponse({}, { ok: false, status: 502, text: '<html>Bad gateway</html>' }),
    });
    const b = renderPage();
    attach(txt());
    await waitFor(() =>
      expect(hx.actions.showNotification).toHaveBeenCalledWith('error', '<html>Bad gateway</html>'),
    );
    b.unmount();

    hx.actions.showNotification.mockClear();
    installFetch({ '/documents/attach': () => fakeResponse({}, { ok: false, status: 500, text: '' }) });
    const c = renderPage();
    attach(txt());
    await waitFor(() =>
      expect(hx.actions.showNotification).toHaveBeenCalledWith('error', 'Ошибка при прикреплении файла'),
    );
    c.unmount();

    hx.actions.showNotification.mockClear();
    installFetch({
      '/documents/attach': () => ({
        ok: false,
        status: 500,
        statusText: 'x',
        headers: { get: () => null },
        text: () => Promise.reject(new Error('stream')),
      }),
    });
    renderPage();
    attach(txt());
    await waitFor(() =>
      expect(hx.actions.showNotification).toHaveBeenCalledWith('error', 'Ошибка при прикреплении файла'),
    );
  });

  it('JSON без поля detail оставляет текст по умолчанию', async () => {
    installFetch({ '/documents/attach': () => fakeResponse({}, { ok: false, status: 500, text: '{"a":1}' }) });
    renderPage();
    attach(txt());
    await waitFor(() =>
      expect(hx.actions.showNotification).toHaveBeenCalledWith('error', 'Ошибка при прикреплении файла'),
    );
  });

  it('успешный ответ с битым JSON даёт тост и не добавляет вложение', async () => {
    installFetch({ '/documents/attach': () => fakeResponse({}, { badJson: true }) });
    renderPage();
    attach(txt());
    await waitFor(() =>
      expect(hx.actions.showNotification).toHaveBeenCalledWith(
        'error',
        'Сервер вернул некорректный ответ при прикреплении файла',
      ),
    );
    expect(screen.queryByTestId('ib-remove-0')).toBeNull();
    expect(hx.misc.logChatAttachError).toHaveBeenCalledWith(
      'attach-json-parse-failed',
      expect.any(Error),
      expect.any(Object),
    );
    await waitFor(() => expect(screen.getByTestId('input-bar')).toHaveAttribute('data-uploading', 'false'));
  });

  it('сетевая ошибка и сбой подготовки изображения дают «Не удалось прикрепить файл»', async () => {
    installFetch({
      '/documents/attach': () => {
        throw new Error('network');
      },
    });
    const a = renderPage();
    attach(txt());
    await waitFor(() =>
      expect(hx.actions.showNotification).toHaveBeenCalledWith('error', 'Не удалось прикрепить файл'),
    );
    expect(hx.misc.logChatAttachError).toHaveBeenCalledWith(
      'attach-unhandled-error',
      expect.any(Error),
      expect.objectContaining({ name: 'a.txt' }),
    );
    a.unmount();

    hx.actions.showNotification.mockClear();
    hx.inlineImage.prepareInlineImageFile.mockRejectedValue(new Error('canvas'));
    renderPage();
    attach(new File(['x'], 'p.png', { type: 'image/png' }));
    await waitFor(() =>
      expect(hx.actions.showNotification).toHaveBeenCalledWith('error', 'Не удалось прикрепить файл'),
    );
    expect((URL as any).revokeObjectURL).toHaveBeenCalled();
  });

  it('пустой выбор файлов ничего не загружает', () => {
    const fetchMock = installFetch();
    renderPage();
    fireEvent.click(screen.getByTestId('ib-file-empty'));
    fireEvent.change(screen.getByTestId('input-file'), { target: { files: [] } });
    expect(uploadCall(fetchMock)).toBeUndefined();
  });

  it('кнопка «Прикрепить» открывает системный выбор файла', () => {
    renderPage();
    const fileInput = screen.getByTestId('input-file') as HTMLInputElement;
    const click = jest.spyOn(fileInput, 'click').mockImplementation(() => {});
    fireEvent.click(screen.getByTestId('ib-attach'));
    expect(click).toHaveBeenCalled();
  });
});

describe('UnifiedChatPage: перетаскивание файла на область чата', () => {
  it('drop с файлом запускает загрузку первого файла', async () => {
    const fetchMock = installFetch({
      '/documents/attach': () => fakeResponse({ type: 'text', content: 'D', filename: 'dropped.txt' }),
    });
    const { container } = renderPage();
    const area = container.querySelector('.chat-messages-area') as HTMLElement;
    const file = txt('dropped.txt');
    fireEvent.drop(area, { dataTransfer: { files: [file], items: [], types: ['Files'] } });
    await screen.findByTestId('ib-remove-0');
    expect(uploadCall(fetchMock)![1].body.get('file').name).toBe('dropped.txt');
  });

  it('drop на области поля ввода тоже обрабатывается', async () => {
    installFetch({
      '/documents/attach': () => fakeResponse({ type: 'text', content: 'D', filename: 'in.txt' }),
    });
    const { container } = renderPage();
    const area = container.querySelector('.chat-input-area') as HTMLElement;
    fireEvent.dragOver(area, { dataTransfer: { items: [{ kind: 'file' }], types: ['Files'], files: [] } });
    fireEvent.dragLeave(area, { dataTransfer: { items: [{ kind: 'file' }], types: ['Files'], files: [] } });
    fireEvent.drop(area, { dataTransfer: { files: [txt('in.txt')], items: [], types: ['Files'] } });
    await screen.findByTestId('ib-remove-0');
  });
});

function type(v: string) {
  fireEvent.change(input(), { target: { value: v } });
}

describe('UnifiedChatPage: вставка из буфера обмена', () => {
  const paste = (data: { text?: string }) => {
    const ev = { clipboardData: { getData: () => data.text ?? '', files: [], items: [] } };
    return fireEvent.paste(input(), ev);
  };

  it('скриншот из буфера прикрепляется как файл, стандартная вставка отменяется', async () => {
    installFetch({
      '/documents/attach': () => fakeResponse({ type: 'image', content: 'IMG', filename: 'shot.png' }),
    });
    const shot = new File(['x'], 'shot.png', { type: 'image/png' });
    hx.inlineImage.getClipboardImageFile.mockReturnValue(shot);
    renderPage();
    const notPrevented = paste({});
    expect(notPrevented).toBe(false);
    await screen.findByTestId('ib-remove-0');
  });

  it('во время загрузки скриншот отклоняется с предупреждением', async () => {
    let release: (v: unknown) => void = () => {};
    installFetch({
      '/documents/attach': () =>
        new Promise((r) => {
          release = r;
        }),
    });
    renderPage();
    attach(txt());
    expect(screen.getByTestId('input-bar')).toHaveAttribute('data-uploading', 'true');
    hx.inlineImage.getClipboardImageFile.mockReturnValue(new File(['x'], 's.png', { type: 'image/png' }));
    paste({});
    expect(hx.actions.showNotification).toHaveBeenCalledWith('warning', 'Дождитесь окончания загрузки');
    await act(async () => {
      release(fakeResponse({}, { ok: false, status: 500, text: '' }));
    });
  });

  it('когда прикрепление недоступно (ждём токены), скриншот не принимается', () => {
    hx.state.loadingChatIds = ['c1'];
    hx.messages = [userMsg({ id: 'u1' }), msg({ id: 'a1', content: '', isStreaming: true })];
    hx.inlineImage.getClipboardImageFile.mockReturnValue(new File(['x'], 's.png', { type: 'image/png' }));
    renderPage();
    paste({});
    expect(hx.actions.showNotification).toHaveBeenCalledWith('warning', 'Сейчас нельзя прикрепить файл');
  });

  it('большой текст при включённой настройке превращается в файл, поле очищается', async () => {
    localStorage.setItem('large_text_as_file', 'true');
    const fetchMock = installFetch({
      '/documents/attach': () => fakeResponse({ type: 'text', content: 'L', filename: 'pasted.txt' }),
    });
    renderPage();
    type('старое');
    const notPrevented = paste({ text: 'я'.repeat(1001) });
    expect(notPrevented).toBe(false);
    await waitFor(() =>
      expect(hx.actions.showNotification).toHaveBeenCalledWith('success', 'Большой текст вставлен как файл'),
    );
    expect(uploadCall(fetchMock)![1].body.get('file').name).toMatch(/^pasted_text_\d+\.txt$/);
    expect(input()).toHaveValue('');
  });

  it('короткий текст или выключенная настройка — обычная вставка', () => {
    localStorage.setItem('large_text_as_file', 'true');
    const { unmount } = renderPage();
    expect(paste({ text: 'коротко' })).toBe(true);
    unmount();

    localStorage.removeItem('large_text_as_file');
    renderPage();
    expect(paste({ text: 'я'.repeat(2000) })).toBe(true);
  });

  it('сбой при создании файла из текста даёт тост об ошибке', async () => {
    localStorage.setItem('large_text_as_file', 'true');
    hx.misc.logChatAttach.mockImplementation(() => {
      throw new Error('log failed');
    });
    renderPage();
    paste({ text: 'я'.repeat(1500) });
    await waitFor(() =>
      expect(hx.actions.showNotification).toHaveBeenCalledWith(
        'error',
        'Ошибка при создании файла из вставленного текста',
      ),
    );
  });
});
