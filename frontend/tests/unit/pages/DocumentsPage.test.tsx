import React from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import DocumentsPage from '@/pages/DocumentsPage';

// DocumentsPage нигде не импортируется (не подключена к App), но покрываем её.
const mockNotify = jest.fn();
jest.mock('@/contexts/AppContext', () => ({
  useAppActions: () => ({ showNotification: mockNotify }),
}));
jest.mock('@/config/api', () => ({
  authFetch: (...a: unknown[]) => (require('@tests/unit/helpers/authFetchMock').authFetchMock as (...x: unknown[]) => unknown)(...a),
  getApiUrl: (p: string) => `http://api${p}`,
  API_ENDPOINTS: { DOCUMENTS_UPLOAD: '/api/documents/upload', DOCUMENTS_QUERY: '/api/documents/query' },
}));

const fetchMock = jest.fn();
const PDF = 'application/pdf';
const DOCX = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document';
const XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';

function makeFile(name = 'a.pdf', type = PDF, size = 2048) {
  const f = new File(['x'], name, { type });
  Object.defineProperty(f, 'size', { value: size });
  return f;
}
const res = (ok: boolean, body: any) => ({ ok, json: async () => body });

async function openDialog() {
  fireEvent.click(screen.getAllByText('Загрузить документ')[0]);
  return screen.getByRole('dialog');
}
function fileInput(): HTMLInputElement {
  return document.querySelector('input[type="file"]') as HTMLInputElement;
}
async function upload(file: File) {
  await openDialog();
  await act(async () => {
    fireEvent.change(fileInput(), { target: { files: [file] } });
  });
}

describe('DocumentsPage', () => {
  beforeEach(() => {
    mockNotify.mockReset();
    fetchMock.mockReset();
    (global as any).fetch = fetchMock;
    (URL as any).createObjectURL = jest.fn(() => 'blob:x');
    (URL as any).revokeObjectURL = jest.fn();
    jest.spyOn(console, 'error').mockImplementation(() => {});
  });
  afterEach(() => jest.restoreAllMocks());

  it('пустая страница: подсказка, запрос заблокирован', () => {
    render(<DocumentsPage />);
    expect(screen.getByText('Документы не загружены')).toBeTruthy();
    expect(screen.getAllByText('Сначала загрузите документ').length).toBeGreaterThan(0);
    expect((screen.getByText('Задать вопрос').closest('button') as HTMLButtonElement).disabled).toBe(true);
    expect(screen.queryByText('Примеры вопросов:')).toBeNull();
  });

  it('кнопка «Загрузить первый документ» открывает диалог, «Отмена» закрывает', async () => {
    render(<DocumentsPage />);
    fireEvent.click(screen.getByText('Загрузить первый документ'));
    const dialog = screen.getByRole('dialog');
    fireEvent.click(within(dialog).getByText('Отмена'));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('клик по зоне загрузки открывает выбор файла', async () => {
    render(<DocumentsPage />);
    const dialog = await openDialog();
    const clickSpy = jest.spyOn(fileInput(), 'click');
    fireEvent.click(within(dialog).getByText('Перетащите файл сюда или нажмите для выбора'));
    expect(clickSpy).toHaveBeenCalled();
  });

  it('успешная загрузка: FormData на upload, файл в списке, диалог закрыт', async () => {
    fetchMock.mockResolvedValue(res(true, {}));
    render(<DocumentsPage />);
    await upload(makeFile('отчёт.pdf', PDF, 2048));
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('http://api/api/documents/upload');
    expect(init.method).toBe('POST');
    expect((init.body as FormData).get('file')).toBeInstanceOf(File);
    expect(mockNotify).toHaveBeenCalledWith('success', 'Документ успешно загружен и обработан');
    expect(screen.getByText('отчёт.pdf')).toBeTruthy();
    expect(screen.getByText('2 KB')).toBeTruthy();
    expect(screen.getByText('Примеры вопросов:')).toBeTruthy();
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('иконки зависят от типа файла (pdf / word / excel / прочее) и размеры форматируются', async () => {
    fetchMock.mockResolvedValue(res(true, {}));
    render(<DocumentsPage />);
    await upload(makeFile('a.pdf', PDF, 0));
    await upload(makeFile('b.docx', DOCX, 5 * 1024 * 1024));
    await upload(makeFile('c.xlsx', XLSX, 1536));
    await upload(makeFile('d.png', 'image/png', 10));
    expect(screen.getByTestId('PictureAsPdfIcon')).toBeTruthy();
    expect(screen.getByTestId('TableChartIcon')).toBeTruthy();
    expect(screen.getAllByTestId('DescriptionIcon').length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText('0 Byte')).toBeTruthy();
    expect(screen.getByText('5 MB')).toBeTruthy();
    expect(screen.getByText('1.5 KB')).toBeTruthy();
    expect(screen.getByText('10 Bytes')).toBeTruthy();
  });

  it('недопустимый тип файла отклоняется без запроса', async () => {
    render(<DocumentsPage />);
    await upload(makeFile('x.exe', 'application/x-msdownload'));
    expect(fetchMock).not.toHaveBeenCalled();
    expect(mockNotify).toHaveBeenCalledWith(
      'error',
      'Поддерживаются только файлы PDF, Word (.docx), Excel (.xlsx) и изображения (JPG, PNG, WebP)',
    );
  });

  it('файл больше 50 МБ отклоняется', async () => {
    render(<DocumentsPage />);
    await upload(makeFile('big.pdf', PDF, 50 * 1024 * 1024 + 1));
    expect(fetchMock).not.toHaveBeenCalled();
    expect(mockNotify).toHaveBeenCalledWith('error', 'Размер файла не должен превышать 50MB');
  });

  it('пустой выбор файла (отмена диалога ОС) ничего не делает', async () => {
    render(<DocumentsPage />);
    await openDialog();
    await act(async () => {
      fireEvent.change(fileInput(), { target: { files: [] } });
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('ошибка сервера при загрузке показывает detail, при невалидном JSON — запасной текст', async () => {
    fetchMock.mockResolvedValueOnce(res(false, { detail: 'Слишком большой' }));
    render(<DocumentsPage />);
    await upload(makeFile());
    expect(mockNotify).toHaveBeenCalledWith('error', 'Слишком большой');
    fetchMock.mockResolvedValueOnce({ ok: false, json: async () => { throw new Error('bad json'); } });
    await act(async () => {
      fireEvent.change(fileInput(), { target: { files: [makeFile()] } });
    });
    expect(mockNotify).toHaveBeenLastCalledWith('error', 'Ошибка загрузки');
    fetchMock.mockResolvedValueOnce(res(false, {}));
    await act(async () => {
      fireEvent.change(fileInput(), { target: { files: [makeFile()] } });
    });
    expect(mockNotify).toHaveBeenLastCalledWith('error', 'Ошибка при загрузке документа');
    expect(screen.getByText('Документы не загружены')).toBeTruthy();
  });

  it('сетевая ошибка при загрузке показывает уведомление и снимает индикатор', async () => {
    fetchMock.mockRejectedValue(new Error('net'));
    render(<DocumentsPage />);
    await upload(makeFile());
    expect(mockNotify).toHaveBeenCalledWith('error', 'Ошибка при загрузке документа');
    expect((screen.getAllByText('Загрузить документ')[0].closest('button') as HTMLButtonElement).disabled).toBe(false);
  });

  it('во время загрузки виден индикатор и кнопка «Отмена» заблокирована', async () => {
    let resolve!: (v: any) => void;
    fetchMock.mockReturnValue(new Promise((r) => { resolve = r; }));
    render(<DocumentsPage />);
    await upload(makeFile());
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByText('Загружаю и обрабатываю документ...')).toBeTruthy();
    expect((within(dialog).getByText('Отмена') as HTMLButtonElement).disabled).toBe(true);
    await act(async () => { resolve(res(true, {})); });
    expect(screen.queryByText('Загружаю и обрабатываю документ...')).toBeNull();
  });

  it('drag&drop: подсветка при перетаскивании, сброс при уходе, загрузка первого файла при drop', async () => {
    fetchMock.mockResolvedValue(res(true, {}));
    render(<DocumentsPage />);
    const dialog = await openDialog();
    const zone = within(dialog).getByText('Перетащите файл сюда или нажмите для выбора').parentElement!;
    fireEvent.dragOver(zone);
    expect(getComputedStyle(zone).borderColor).not.toBe('');
    fireEvent.dragLeave(zone);
    await act(async () => {
      fireEvent.drop(zone, { dataTransfer: { files: [makeFile('drop.pdf'), makeFile('second.pdf')] } });
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(screen.getByText('drop.pdf')).toBeTruthy();
    expect(screen.queryByText('second.pdf')).toBeNull();
  });

  it('drop без файлов игнорируется', async () => {
    render(<DocumentsPage />);
    const dialog = await openDialog();
    const zone = within(dialog).getByText('Перетащите файл сюда или нажмите для выбора').parentElement!;
    await act(async () => {
      fireEvent.drop(zone, { dataTransfer: { files: [] } });
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  describe('запросы к документу', () => {
    async function withDoc() {
      fetchMock.mockResolvedValueOnce(res(true, {}));
      render(<DocumentsPage />);
      await upload(makeFile());
      await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
      fetchMock.mockClear();
      mockNotify.mockClear();
    }
    const ask = () => screen.getByText('Задать вопрос').closest('button') as HTMLButtonElement;
    const box = () => screen.getByPlaceholderText(/Например:/) as HTMLTextAreaElement;

    it('примеры вопросов подставляются в поле, кнопка включается', async () => {
      await withDoc();
      expect(ask().disabled).toBe(true);
      fireEvent.click(screen.getByText('Какие основные выводы?'));
      expect(box().value).toBe('Какие основные выводы?');
      expect(ask().disabled).toBe(false);
    });

    it('успешный запрос показывает ответ и прячет примеры', async () => {
      await withDoc();
      fetchMock.mockResolvedValueOnce(res(true, { response: 'Итог\nвторая строка' }));
      fireEvent.change(box(), { target: { value: 'что тут?' } });
      await act(async () => { fireEvent.click(ask()); });
      const [url, init] = fetchMock.mock.calls[0];
      expect(url).toBe('http://api/api/documents/query');
      expect(init.method).toBe('POST');
      expect(JSON.parse(init.body)).toEqual({ query: 'что тут?' });
      expect(screen.getByText(/Итог/).textContent).toBe('Итог\nвторая строка');
      expect(mockNotify).toHaveBeenCalledWith('success', 'Ответ получен');
      expect(screen.queryByText('Примеры вопросов:')).toBeNull();
    });

    it('во время запроса виден индикатор и поле заблокировано', async () => {
      await withDoc();
      let resolve!: (v: any) => void;
      fetchMock.mockReturnValueOnce(new Promise((r) => { resolve = r; }));
      fireEvent.change(box(), { target: { value: 'q' } });
      await act(async () => { fireEvent.click(ask()); });
      expect(screen.getByText('Анализирую документ и формирую ответ...')).toBeTruthy();
      expect(box().disabled).toBe(true);
      await act(async () => { resolve(res(true, { response: 'ok' })); });
      expect(screen.queryByText('Анализирую документ и формирую ответ...')).toBeNull();
    });

    it('ошибка запроса: detail / запасной JSON / сеть', async () => {
      await withDoc();
      fireEvent.change(box(), { target: { value: 'q' } });
      fetchMock.mockResolvedValueOnce(res(false, { detail: 'Нет индекса' }));
      await act(async () => { fireEvent.click(ask()); });
      expect(mockNotify).toHaveBeenLastCalledWith('error', 'Нет индекса');
      fetchMock.mockResolvedValueOnce({ ok: false, json: async () => { throw new Error('x'); } });
      await act(async () => { fireEvent.click(ask()); });
      expect(mockNotify).toHaveBeenLastCalledWith('error', 'Ошибка обработки');
      fetchMock.mockResolvedValueOnce(res(false, {}));
      await act(async () => { fireEvent.click(ask()); });
      expect(mockNotify).toHaveBeenLastCalledWith('error', 'Ошибка при обработке запроса');
      fetchMock.mockRejectedValueOnce(new Error('net'));
      await act(async () => { fireEvent.click(ask()); });
      expect(mockNotify).toHaveBeenLastCalledWith('error', 'Ошибка при отправке запроса');
    });

    it('копирование ответа: успех и отказ', async () => {
      await withDoc();
      fetchMock.mockResolvedValueOnce(res(true, { response: 'ответ' }));
      fireEvent.change(box(), { target: { value: 'q' } });
      await act(async () => { fireEvent.click(ask()); });
      const writeText = jest.fn().mockResolvedValueOnce(undefined).mockRejectedValueOnce(new Error('no'));
      Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
      await act(async () => { fireEvent.click(screen.getByText('Копировать')); });
      expect(writeText).toHaveBeenCalledWith('ответ');
      expect(mockNotify).toHaveBeenLastCalledWith('success', 'Ответ скопирован в буфер обмена');
      await act(async () => { fireEvent.click(screen.getByText('Копировать')); });
      expect(mockNotify).toHaveBeenLastCalledWith('error', 'Не удалось скопировать ответ');
    });

    it('«Скачать ответ» сохраняет answer.txt', async () => {
      await withDoc();
      fetchMock.mockResolvedValueOnce(res(true, { response: 'ответ' }));
      fireEvent.change(box(), { target: { value: 'q' } });
      await act(async () => { fireEvent.click(ask()); });
      let download = '';
      jest.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
        download = this.download;
      });
      fireEvent.click(screen.getByText('Скачать ответ'));
      expect(download).toBe('answer.txt');
      expect((URL.createObjectURL as jest.Mock).mock.calls[0][0].type).toBe('text/plain');
      expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:x');
    });
  });

  it('защитные ветки handleQuery недостижимы через UI: кнопка заблокирована при пустом вопросе / без файлов', async () => {
    render(<DocumentsPage />);
    const btn = screen.getByText('Задать вопрос').closest('button') as HTMLButtonElement;
    fireEvent.click(btn);
    expect(mockNotify).not.toHaveBeenCalled();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('кнопка удаления документа в списке — заглушка без обработчика (known issue)', async () => {
    // Как правильно: кнопка корзины должна удалять документ (запрос на backend + удаление из списка).
    fetchMock.mockResolvedValue(res(true, {}));
    render(<DocumentsPage />);
    await upload(makeFile('keep.pdf'));
    fetchMock.mockClear();
    fireEvent.click(screen.getByTestId('DeleteIcon').closest('button')!);
    expect(screen.getByText('keep.pdf')).toBeTruthy();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('запросы загрузки и поиска идут без заголовка авторизации (known issue)', async () => {
    // Как правильно: как и остальные страницы, использовать getAuthFetchHeaders() — иначе при
    // включённой авторизации backend ответит 401.
    fetchMock.mockResolvedValue(res(true, { response: 'r' }));
    render(<DocumentsPage />);
    await upload(makeFile());
    expect(fetchMock.mock.calls[0][1].headers).toBeUndefined();
  });

  it('Esc закрывает диалог загрузки', async () => {
    render(<DocumentsPage />);
    const dialog = await openDialog();
    fireEvent.keyDown(dialog, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });
});
