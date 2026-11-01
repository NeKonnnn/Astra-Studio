import React from 'react';
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import KnowledgeBasePage from '@/pages/KnowledgeBasePage';

// KnowledgeBasePage нигде не импортируется (не подключена к App), но покрываем её.
const mockNotify = jest.fn();
jest.mock('@/contexts/AppContext', () => ({
  useAppActions: () => ({ showNotification: mockNotify }),
}));
jest.mock('@/config/api', () => ({
  authFetch: (...a: unknown[]) => (require('@tests/unit/helpers/authFetchMock').authFetchMock as (...x: unknown[]) => unknown)(...a),
  getApiUrl: (p: string) => `http://api${p}`,
  getAuthFetchHeaders: () => ({ Authorization: 'Bearer t' }),
  API_ENDPOINTS: {
    KB_DOCUMENTS_UPLOAD: '/api/kb/documents',
    KB_DOCUMENTS_LIST: '/api/kb/documents',
    KB_DOCUMENTS_DELETE: '/api/kb/documents',
  },
}));
jest.mock('@/utils/ragPendingUpload', () => ({
  parseRagDuplicateUploadMessage: (status: number, _err: any, name: string) =>
    status === 409 ? `Дубликат: ${name}` : null,
  formatRagApiDetail: (detail: unknown, fb: string) => (typeof detail === 'string' && detail ? detail : fb),
}));

const fetchMock = jest.fn();
const json = (data: any) => ({ ok: true, status: 200, json: async () => data });
const fail = (status: number, body: any, statusText = 'ERR') => ({
  ok: false,
  status,
  statusText,
  json: async () => body,
});

const docs = [
  { id: 1, filename: 'отчёт.pdf', created_at: '2025-03-04T10:20:00Z', size: 500, file_type: 'pdf' },
  { id: 2, filename: 'таблица.xlsx', created_at: null, size: 2048, file_type: null },
  { id: 3, filename: 'заметки.txt', created_at: '2025-03-04T10:20:00Z', size: 3 * 1024 * 1024, file_type: null },
  { id: 4, filename: 'a.docx', created_at: null, size: null, file_type: null },
  { id: 5, filename: 'scan.PNG', created_at: null, size: 1, file_type: null },
  { id: 6, filename: 'data.csv', created_at: null, size: 1, file_type: null },
  { id: 7, filename: 'slides.pptx', created_at: null, size: 1, file_type: null },
];

async function renderPage(list: any = { documents: docs }, dark = false) {
  fetchMock.mockResolvedValueOnce(json(list));
  const utils = render(<KnowledgeBasePage isDarkMode={dark} />);
  await act(async () => {});
  return utils;
}
const fileInput = () => document.querySelector('input[type="file"]') as HTMLInputElement;
const file = (name: string) => new File(['x'], name);

describe('KnowledgeBasePage', () => {
  beforeEach(() => {
    jest.useFakeTimers('modern');
    mockNotify.mockReset();
    fetchMock.mockReset();
    (global as any).fetch = fetchMock;
  });
  afterEach(() => {
    jest.useRealTimers();
    jest.restoreAllMocks();
  });

  it('загружает список БЗ и показывает счётчик, типы файлов, размеры и подсказку', async () => {
    await renderPage();
    expect(fetchMock).toHaveBeenCalledWith('http://api/api/kb/documents');
    expect(screen.getByText('отчёт.pdf')).toBeTruthy();
    expect(screen.getByText('PDF')).toBeTruthy();
    expect(screen.getAllByText('Excel').length).toBe(1);
    expect(screen.getByText('Word')).toBeTruthy();
    expect(screen.getAllByText('Image (OCR)').length).toBe(1);
    expect(screen.getByText('TXT')).toBeTruthy();
    expect(screen.getByText('CSV')).toBeTruthy();
    expect(screen.getByText('PPTX')).toBeTruthy();
    expect(screen.getByText(/· 500 Б$/)).toBeTruthy();
    expect(screen.getByText(/· 2\.0 КБ$/)).toBeTruthy();
    expect(screen.getByText(/· 3\.0 МБ$/)).toBeTruthy();
    expect(screen.getByText(/— · —$/)).toBeTruthy();
    expect(screen.getByText('7')).toBeTruthy();
    expect(screen.getByText(/Включите/)).toBeTruthy();
  });

  it('иконки подбираются по расширению', async () => {
    await renderPage();
    expect(screen.getByTestId('PictureAsPdfIcon')).toBeTruthy();
    expect(screen.getAllByTestId('TableChartIcon').length).toBe(2); // xlsx + csv
    expect(screen.getByTestId('TextSnippetIcon')).toBeTruthy();
    expect(screen.getByTestId('ArticleIcon')).toBeTruthy();
    expect(screen.getByTestId('ImageIcon')).toBeTruthy();
    // pptx: иконка по умолчанию
    expect(document.querySelectorAll('[data-testid="DescriptionIcon"]').length).toBe(1);
  });

  it('ответ-массив без обёртки documents тоже поддерживается', async () => {
    await renderPage([docs[0]]);
    expect(screen.getByText('отчёт.pdf')).toBeTruthy();
  });


  it('пустой список показывает «База Знаний пуста» без подсказки', async () => {
    await renderPage({ documents: [] });
    expect(screen.getByText('База Знаний пуста')).toBeTruthy();
    expect(screen.queryByText(/Включите/)).toBeNull();
  });

  it('ответ-объект без массива documents роняет рендер списка (known issue)', async () => {
    // Как правильно: при отсутствии/null в поле documents список должен стать пустым массивом.
    // Сейчас `data.documents || data` кладёт в состояние сам объект ответа, и documents.map падает.
    jest.spyOn(console, 'error').mockImplementation(() => {});
    class Boundary extends React.Component<{ children: React.ReactNode }, { err: string }> {
      state = { err: '' };
      static getDerivedStateFromError(e: Error) { return { err: e.message }; }
      render() { return this.state.err ? <div data-testid="crash">{this.state.err}</div> : this.props.children; }
    }
    fetchMock.mockResolvedValueOnce(json({ documents: null }));
    render(<Boundary><KnowledgeBasePage isDarkMode={false} /></Boundary>);
    await act(async () => {});
    expect(screen.getByTestId('crash').textContent).toMatch(/map is not a function/);
  });

  it('ошибка HTTP при загрузке списка уходит в уведомление', async () => {
    fetchMock.mockResolvedValueOnce({ ok: false, status: 500, json: async () => ({}) });
    render(<KnowledgeBasePage isDarkMode />);
    await act(async () => {});
    expect(mockNotify).toHaveBeenCalledWith('error', expect.stringContaining('HTTP 500'));
    expect(screen.getByText('База Знаний пуста')).toBeTruthy();
  });

  it('во время загрузки списка кнопка обновления заблокирована, виден спиннер', async () => {
    fetchMock.mockReturnValueOnce(new Promise(() => {}));
    render(<KnowledgeBasePage isDarkMode={false} />);
    expect((screen.getByTestId('RefreshIcon').closest('button') as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getAllByRole('progressbar').length).toBeGreaterThan(0);
  });

  it('если дата не форматируется, показывается исходная строка', async () => {
    jest.spyOn(Date.prototype, 'toLocaleString').mockImplementation(() => {
      throw new RangeError('bad');
    });
    await renderPage({ documents: [docs[0]] });
    expect(screen.getByText(/2025-03-04T10:20:00Z · 500 Б/)).toBeTruthy();
  });

  it('список и удаление запрашиваются без токена, а загрузка — с токеном (known issue)', async () => {
    // Как правильно: все запросы к /api/kb/documents должны идти с getAuthFetchHeaders();
    // сейчас GET и DELETE уходят без Authorization (только POST загрузки с заголовком).
    await renderPage({ documents: [docs[0]] });
    expect(fetchMock.mock.calls[0]).toHaveLength(1);
    fetchMock.mockResolvedValueOnce(json({})).mockResolvedValueOnce(json({ documents: [] }));
    fireEvent.click(screen.getByTestId('DeleteIcon').closest('button')!);
    await act(async () => {
      fireEvent.click(within(screen.getByRole('dialog')).getByText('Удалить'));
    });
    expect(fetchMock.mock.calls[1][1]).toEqual({ method: 'DELETE' });
  });

  describe('загрузка файлов', () => {
    const changeFiles = async (files: File[]) => {
      await act(async () => {
        fireEvent.change(fileInput(), { target: { files } });
      });
    };

    it('неподдерживаемый формат: сообщение, запросов нет', async () => {
      await renderPage();
      fetchMock.mockClear();
      await changeFiles([file('virus.exe')]);
      expect(fetchMock).not.toHaveBeenCalled();
      expect(screen.getByText(/Неподдерживаемый формат/)).toBeTruthy();
    });

    it('пустой выбор ничего не делает', async () => {
      await renderPage();
      fetchMock.mockClear();
      await changeFiles([]);
      expect(fetchMock).not.toHaveBeenCalled();
      expect(screen.queryByText(/Неподдерживаемый формат/)).toBeNull();
    });

    it('успех: отфильтровывает лишние файлы, шлёт POST с токеном, итог и перезагрузка списка', async () => {
      await renderPage();
      fetchMock.mockClear();
      fetchMock
        .mockResolvedValueOnce(json({}))
        .mockResolvedValueOnce(json({}))
        .mockResolvedValueOnce(json({ documents: [docs[0]] }));
      await changeFiles([file('a.pdf'), file('bad.zip'), file('b.TXT')]);
      const posts = fetchMock.mock.calls.filter((c) => c[1]?.method === 'POST');
      expect(posts).toHaveLength(2);
      expect(posts[0][0]).toBe('http://api/api/kb/documents');
      expect(posts[0][1].headers).toEqual({ Authorization: 'Bearer t' });
      expect((posts[0][1].body as FormData).get('file')).toHaveProperty('name', 'a.pdf');
      expect((posts[1][1].body as FormData).get('file')).toHaveProperty('name', 'b.TXT');
      expect(screen.getByText('Успешно загружено 2 документ(ов)')).toBeTruthy();
      expect(fetchMock).toHaveBeenCalledTimes(3); // 2 POST + перезагрузка списка
      expect(fileInput().value).toBe('');
    });

    it('прогресс: ✓ у готового файла, спиннер у текущего, зона загрузки неактивна', async () => {
      await renderPage();
      fetchMock.mockClear();
      let finishSecond!: (v: any) => void;
      fetchMock
        .mockResolvedValueOnce(json({}))
        .mockReturnValueOnce(new Promise((r) => { finishSecond = r; }));
      await changeFiles([file('a.pdf'), file('b.pdf')]);
      expect(screen.getByText('Индексирую документы...')).toBeTruthy();
      expect(screen.getByText('✓ a.pdf')).toBeTruthy();
      expect(screen.getByText('Загружаю: b.pdf...')).toBeTruthy();
      expect(screen.getByTestId('CheckCircleIcon')).toBeTruthy();
      // клик по зоне во время загрузки не открывает выбор файла
      const clickSpy = jest.spyOn(fileInput(), 'click');
      fireEvent.click(screen.getByText('Индексирую документы...'));
      expect(clickSpy).not.toHaveBeenCalled();
      fetchMock.mockResolvedValueOnce(json({ documents: [] }));
      await act(async () => { finishSecond(json({})); });
      expect(screen.queryByText('Индексирую документы...')).toBeNull();
    });

    it('ошибка сервера: detail попадает в прогресс и итоговое сообщение', async () => {
      await renderPage();
      fetchMock.mockClear();
      let finishSecond!: (v: any) => void;
      fetchMock
        .mockResolvedValueOnce(fail(500, { detail: 'Плохой PDF' }))
        .mockReturnValueOnce(new Promise((r) => { finishSecond = r; }));
      await changeFiles([file('a.pdf'), file('b.pdf')]);
      expect(screen.getByText('✗ a.pdf: Плохой PDF')).toBeTruthy();
      expect(screen.getByTestId('ErrorOutlineIcon')).toBeTruthy();
      fetchMock.mockResolvedValueOnce(json({ documents: [] }));
      await act(async () => { finishSecond(json({})); });
      expect(screen.getByText('Ошибки: a.pdf: Плохой PDF')).toBeTruthy();
    });

    it('при невалидном JSON ошибки берётся statusText; сетевой сбой даёт message', async () => {
      await renderPage();
      fetchMock.mockClear();
      fetchMock
        .mockResolvedValueOnce({ ok: false, status: 502, statusText: 'Bad Gateway', json: async () => { throw new Error('x'); } })
        .mockRejectedValueOnce(new Error('net down'))
        .mockResolvedValueOnce(json({ documents: [] }));
      await changeFiles([file('a.pdf'), file('b.pdf')]);
      expect(screen.getByText('Ошибки: a.pdf: Bad Gateway; b.pdf: net down')).toBeTruthy();
    });

    it('только дубликаты: предупреждение с текстом первого дубликата', async () => {
      await renderPage();
      fetchMock.mockClear();
      let finishSecond!: (v: any) => void;
      fetchMock
        .mockResolvedValueOnce(fail(409, { detail: 'dup' }))
        .mockReturnValueOnce(new Promise((r) => { finishSecond = r; }));
      await changeFiles([file('a.pdf'), file('b.pdf')]);
      expect(screen.getByText('⚠ a.pdf: уже в базе')).toBeTruthy();
      fetchMock.mockResolvedValueOnce(json({ documents: [] }));
      await act(async () => { finishSecond(fail(409, {})); });
      expect(screen.getByText('Дубликат: a.pdf')).toBeTruthy();
    });

    it('дубликаты вместе с успехами: сводка со счётчиками', async () => {
      await renderPage();
      fetchMock.mockClear();
      fetchMock
        .mockResolvedValueOnce(json({}))
        .mockResolvedValueOnce(fail(409, {}))
        .mockResolvedValueOnce(json({ documents: [] }));
      await changeFiles([file('a.pdf'), file('b.pdf')]);
      expect(screen.getByText('Загружено: 1. Пропущено дубликатов: 1')).toBeTruthy();
    });

    it('предупреждение «уже в базе» отображается спиннером, как незавершённая загрузка (known issue)', async () => {
      // Как правильно: для записи с префиксом «⚠» нужна своя иконка (предупреждение), а не CircularProgress,
      // т.к. сейчас ветки иконки знают только «✓» и «✗».
      await renderPage();
      fetchMock.mockClear();
      let finishSecond!: (v: any) => void;
      fetchMock
        .mockResolvedValueOnce(fail(409, {}))
        .mockReturnValueOnce(new Promise((r) => { finishSecond = r; }));
      await changeFiles([file('a.pdf'), file('b.pdf')]);
      const item = screen.getByText('⚠ a.pdf: уже в базе').closest('li')!;
      expect(item.querySelector('.MuiCircularProgress-root')).not.toBeNull();
      fetchMock.mockResolvedValueOnce(json({ documents: [] }));
      await act(async () => { finishSecond(json({})); });
    });

    it('клик по зоне открывает выбор файлов', async () => {
      await renderPage();
      const clickSpy = jest.spyOn(fileInput(), 'click');
      fireEvent.click(screen.getByText('Перетащите файлы или нажмите для выбора'));
      expect(clickSpy).toHaveBeenCalled();
    });

    it('drag&drop: подсказка при перетаскивании, сброс при уходе, загрузка при drop', async () => {
      await renderPage();
      const zone = screen.getByText('Перетащите файлы или нажмите для выбора').closest('.MuiPaper-root')!;
      fireEvent.dragOver(zone);
      expect(screen.getByText('Отпустите файлы для загрузки')).toBeTruthy();
      fireEvent.dragLeave(zone);
      expect(screen.getByText('Перетащите файлы или нажмите для выбора')).toBeTruthy();
      fetchMock.mockClear();
      fetchMock.mockResolvedValueOnce(json({})).mockResolvedValueOnce(json({ documents: [] }));
      await act(async () => {
        fireEvent.drop(zone, { dataTransfer: { files: [file('d.pdf')] } });
      });
      expect(fetchMock.mock.calls[0][1].method).toBe('POST');
      expect(screen.getByText('Успешно загружено 1 документ(ов)')).toBeTruthy();
    });

    it('drop без файлов игнорируется', async () => {
      await renderPage();
      fetchMock.mockClear();
      const zone = screen.getByText('Перетащите файлы или нажмите для выбора').closest('.MuiPaper-root')!;
      await act(async () => {
        fireEvent.drop(zone, { dataTransfer: { files: [] } });
      });
      expect(fetchMock).not.toHaveBeenCalled();
    });
  });

  describe('удаление и уведомления', () => {
    const openDelete = () => fireEvent.click(screen.getAllByTestId('DeleteIcon')[0].closest('button')!);

    it('диалог показывает имя файла; «Отмена» закрывает без запроса', async () => {
      await renderPage({ documents: [docs[0]] });
      fetchMock.mockClear();
      openDelete();
      const dialog = screen.getByRole('dialog');
      expect(within(dialog).getByText('«отчёт.pdf»')).toBeTruthy();
      fireEvent.click(within(dialog).getByText('Отмена'));
      act(() => { jest.advanceTimersByTime(1000); });
      expect(screen.queryByRole('dialog')).toBeNull();
      expect(fetchMock).not.toHaveBeenCalled();
    });

    it('удаление: DELETE по id, уведомление и перезагрузка списка', async () => {
      await renderPage({ documents: [docs[0]] });
      fetchMock.mockClear();
      fetchMock.mockResolvedValueOnce(json({})).mockResolvedValueOnce(json({ documents: [] }));
      openDelete();
      await act(async () => {
        fireEvent.click(within(screen.getByRole('dialog')).getByText('Удалить'));
      });
      expect(fetchMock.mock.calls[0]).toEqual(['http://api/api/kb/documents/1', { method: 'DELETE' }]);
      expect(screen.getByText('Документ «отчёт.pdf» удалён из Базы Знаний')).toBeTruthy();
      expect(screen.getByText('База Знаний пуста')).toBeTruthy();
    });

    it('повторный клик «Удалить» во время закрытия диалога не шлёт второй запрос', async () => {
      await renderPage({ documents: [docs[0]] });
      fetchMock.mockClear();
      fetchMock.mockResolvedValueOnce(json({})).mockResolvedValueOnce(json({ documents: [] }));
      openDelete();
      const del = within(screen.getByRole('dialog')).getByText('Удалить');
      await act(async () => { fireEvent.click(del); });
      await act(async () => { fireEvent.click(del); });
      expect(fetchMock.mock.calls.filter((c) => c[1]?.method === 'DELETE')).toHaveLength(1);
    });

    it('ошибка удаления показывает сообщение и оставляет документ', async () => {
      await renderPage({ documents: [docs[0]] });
      fetchMock.mockResolvedValueOnce({ ok: false, status: 404, json: async () => ({}) });
      openDelete();
      await act(async () => {
        fireEvent.click(within(screen.getByRole('dialog')).getByText('Удалить'));
      });
      expect(screen.getByText(/Ошибка удаления: Error: HTTP 404/)).toBeTruthy();
      expect(screen.getByText('отчёт.pdf')).toBeTruthy();
    });

    it('снэкбар закрывается по таймеру 4 с и крестиком', async () => {
      await renderPage();
      await act(async () => {
        fireEvent.change(fileInput(), { target: { files: [file('x.zip')] } });
      });
      expect(screen.getByText(/Неподдерживаемый формат/)).toBeTruthy();
      act(() => { jest.advanceTimersByTime(4500); });
      act(() => { jest.advanceTimersByTime(1000); });
      expect(screen.queryByText(/Неподдерживаемый формат/)).toBeNull();
      await act(async () => {
        fireEvent.change(fileInput(), { target: { files: [file('y.zip')] } });
      });
      fireEvent.click(screen.getByRole('button', { name: /close/i }));
      act(() => { jest.advanceTimersByTime(1000); });
      expect(screen.queryByText(/Неподдерживаемый формат/)).toBeNull();
    });

    it('Esc закрывает диалог удаления', async () => {
      await renderPage({ documents: [docs[0]] });
      openDelete();
      fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
      act(() => { jest.advanceTimersByTime(1000); });
      expect(screen.queryByRole('dialog')).toBeNull();
    });
  });

  it('тёмная тема рендерит список без ошибок', async () => {
    await renderPage({ documents: docs }, true);
    expect(screen.getByText('отчёт.pdf')).toBeTruthy();
  });
});
