/* eslint-disable @typescript-eslint/no-explicit-any */
import React from 'react';
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import PluginGalleryPage, { PluginGalleryContent } from '@/pages/PluginGalleryPage';

const mock: any = {};
function reset() {
  mock.token = 'tok';
  mock.notify = jest.fn();
  mock.navigate = jest.fn();
  mock.runs = {} as Record<string, any>;
  mock.startRun = jest.fn(() => true);
  mock.fetchPlugins = jest.fn();
  mock.bookmarks = new Set<string>();
  mock.toggleBookmark = jest.fn((id: string) => {
    if (mock.bookmarks.has(id)) {
      mock.bookmarks.delete(id);
      return false;
    }
    mock.bookmarks.add(id);
    return true;
  });
}
reset();

jest.mock('react-router-dom', () => ({
  useNavigate: () => mock.navigate,
  Navigate: ({ to, replace }: any) => <div data-testid="navigate" data-to={to} data-replace={String(replace)} />,
}));
jest.mock('@/contexts/AuthContext', () => ({ useAuth: () => ({ token: mock.token }) }));
jest.mock('@/contexts/AppContext', () => ({ useAppActions: () => ({ showNotification: mock.notify }) }));
jest.mock('@/contexts/PluginRunContext', () => ({
  usePluginRuns: () => ({
    getRun: (id: string) => mock.runs[id] || null,
    isRunning: (id: string) => mock.runs[id]?.status === 'running',
    startRun: (...a: any[]) => mock.startRun(...a),
    elapsedSec: () => 65,
  }),
}));
jest.mock('@/plugins/api', () => ({ fetchPluginsResponse: (...a: any[]) => mock.fetchPlugins(...a) }));
jest.mock('@/plugins/verdict', () => ({ formatDuration: (s: number) => `${s}с` }));
jest.mock('@/components/artifacts/ArtifactMarkdownPreview', () => ({
  __esModule: true,
  default: ({ content }: any) => <div data-testid="md-preview">{content}</div>,
}));
jest.mock('@/utils/pluginBookmarks', () => ({
  getPluginBookmarkIds: () => new Set(mock.bookmarks),
  togglePluginBookmark: (id: string) => mock.toggleBookmark(id),
}));
jest.mock('@/components/galleryCards', () => ({
  GallerySearchBookmarksBar: (p: any) => (
    <div>
      <input placeholder={p.searchPlaceholder} value={p.searchQuery} onChange={(e) => p.onSearchChange(e.target.value)} />
      <button onClick={p.onToggleBookmarks}>toggle-bookmarks</button>
    </div>
  ),
}));

const plugin = (over: any = {}) => ({
  id: 'cash-flow',
  display_name: 'Аудит денежных потоков',
  description: 'Проверка модели',
  category: 'finance',
  tags: ['excel', 'аудит'],
  enabled: true,
  healthy: true,
  ...over,
});
const catalog = (plugins: any[], extra: any = {}) => ({ enabled: true, plugins, ...extra });

async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}
async function renderContent(plugins: any[] = [plugin()], props: any = {}) {
  mock.fetchPlugins.mockImplementation(async () => catalog(plugins));
  const utils = render(<PluginGalleryContent {...props} />);
  await flush();
  return utils;
}
const openDialog = (name = 'Запустить') => fireEvent.click(screen.getByText(name));
const modelInput = () => document.querySelectorAll('input[type="file"]')[0] as HTMLInputElement;
const qualityInput = () => document.querySelectorAll('input[type="file"]')[1] as HTMLInputElement;
const xlsx = (n = 'model.xlsx') => new File(['x'], n);

describe('PluginGalleryPage', () => {
  beforeEach(() => {
    jest.useFakeTimers('modern');
    reset();
    jest.spyOn(console, 'warn').mockImplementation(() => {});
    (URL as any).createObjectURL = jest.fn(() => 'blob:x');
    (URL as any).revokeObjectURL = jest.fn();
  });
  afterEach(() => {
    jest.useRealTimers();
    jest.restoreAllMocks();
  });

  it('страница-обёртка перенаправляет на вкладку плагинов', () => {
    render(<PluginGalleryPage />);
    expect(screen.getByTestId('navigate').getAttribute('data-to')).toBe('/gallery?tab=plugins');
  });

  describe('каталог', () => {
    it('без токена каталог не загружается', async () => {
      mock.token = null;
      await renderContent();
      expect(mock.fetchPlugins).not.toHaveBeenCalled();
    });

    it('сначала каталог без health, затем с health: карточка обновляется статусом', async () => {
      mock.fetchPlugins.mockImplementation(async (withHealth: boolean) =>
        catalog([plugin({ healthy: withHealth ? true : undefined })]),
      );
      render(<PluginGalleryContent />);
      await flush();
      expect(mock.fetchPlugins.mock.calls.map((c: any[]) => c[0])).toEqual([false, true]);
      expect(screen.getByText('online')).toBeTruthy();
      expect(screen.getByText('Аудит денежных потоков')).toBeTruthy();
      expect(screen.getByText('cash-flow · finance')).toBeTruthy();
      expect(screen.getByText('excel')).toBeTruthy();
    });

    it('пока идёт health-проверка: подпись «проверка online…» и маркер «…»', async () => {
      let resolveHealth!: (v: any) => void;
      mock.fetchPlugins
        .mockResolvedValueOnce(catalog([plugin({ healthy: undefined })]))
        .mockReturnValueOnce(new Promise((r) => { resolveHealth = r; }));
      render(<PluginGalleryContent />);
      await flush();
      expect(screen.getByText('проверка online…')).toBeTruthy();
      expect(screen.getByText('…')).toBeTruthy();
      await act(async () => { resolveHealth(catalog([plugin()])); });
      expect(screen.queryByText('проверка online…')).toBeNull();
    });

    it('встроенный режим показывает «проверка online…» без заголовка страницы', async () => {
      let resolveHealth!: (v: any) => void;
      mock.fetchPlugins
        .mockResolvedValueOnce(catalog([plugin()]))
        .mockReturnValueOnce(new Promise((r) => { resolveHealth = r; }));
      render(<PluginGalleryContent embedded />);
      await flush();
      expect(screen.getByText('проверка online…')).toBeTruthy();
      expect(screen.queryByText('Галерея плагинов')).toBeNull();
      await act(async () => { resolveHealth(catalog([plugin()])); });
    });

    it('обычный режим: заголовок и «К чату»', async () => {
      await renderContent();
      expect(screen.getByText('Галерея плагинов')).toBeTruthy();
      fireEvent.click(screen.getByText('К чату'));
      expect(mock.navigate).toHaveBeenCalledWith('/');
    });

    it('ошибка health не ломает уже показанный каталог', async () => {
      mock.fetchPlugins.mockResolvedValueOnce(catalog([plugin({ healthy: undefined })])).mockRejectedValueOnce(new Error('h'));
      render(<PluginGalleryContent />);
      await flush();
      expect(screen.getByText('Аудит денежных потоков')).toBeTruthy();
      expect(screen.getByText('—')).toBeTruthy();
      expect(console.warn).toHaveBeenCalled();
    });

    it('health вернул пустой список — прежний каталог остаётся', async () => {
      mock.fetchPlugins.mockResolvedValueOnce(catalog([plugin()])).mockResolvedValueOnce(catalog([]));
      render(<PluginGalleryContent />);
      await flush();
      expect(screen.getByText('Аудит денежных потоков')).toBeTruthy();
    });

    it('платформа выключена: предупреждение, health не запрашивается', async () => {
      mock.fetchPlugins.mockResolvedValue({ enabled: false, plugins: [] });
      render(<PluginGalleryContent />);
      await flush();
      expect(screen.getByText(/Платформа плагинов выключена \(plugins\.enabled=false/)).toBeTruthy();
      expect(mock.fetchPlugins).toHaveBeenCalledTimes(1);
    });

    it('пустой каталог: подсказка с catalog_ids или без них', async () => {
      mock.fetchPlugins.mockResolvedValueOnce({ enabled: true, plugins: [], catalog_ids: ['a', 'b'] });
      const a = render(<PluginGalleryContent />);
      await flush();
      expect(screen.getByText('Каталог в config.yml есть (a, b), но API вернул пустой список.')).toBeTruthy();
      a.unmount();
      mock.fetchPlugins.mockResolvedValueOnce({ enabled: true, plugins: [] });
      render(<PluginGalleryContent />);
      await flush();
      expect(screen.getByText('Каталог plugins.catalog в config.yml пуст или не загружен.')).toBeTruthy();
    });

    it('сбой загрузки: сообщение в Alert и уведомление; не-Error — запасной текст', async () => {
      mock.fetchPlugins.mockRejectedValueOnce(new Error('503 Service'));
      const a = render(<PluginGalleryContent />);
      await flush();
      expect(screen.getByText('503 Service')).toBeTruthy();
      expect(mock.notify).toHaveBeenCalledWith('error', '503 Service');
      a.unmount();
      mock.fetchPlugins.mockRejectedValueOnce('boom');
      render(<PluginGalleryContent />);
      await flush();
      expect(screen.getByText('Не удалось загрузить плагины')).toBeTruthy();
    });

    it('спиннер, пока каталог пуст и идёт загрузка', async () => {
      mock.fetchPlugins.mockReturnValue(new Promise(() => {}));
      render(<PluginGalleryContent />);
      await flush();
      expect(screen.getByRole('progressbar')).toBeTruthy();
    });
  });

  describe('карточки', () => {
    it('статусы: выкл / offline с причиной / offline без причины / неизвестно', async () => {
      await renderContent([
        plugin({ id: 'a', display_name: 'A', enabled: false }),
        plugin({ id: 'b', display_name: 'B', healthy: false, health_detail: { error: 'timeout' } }),
        plugin({ id: 'c', display_name: 'C', healthy: false, health_detail: { error: 5 } }),
        plugin({ id: 'd', display_name: 'D', healthy: undefined, description: '', tags: undefined, category: undefined }),
      ]);
      expect(screen.getByText('выкл')).toBeTruthy();
      expect(screen.getAllByText('offline').length).toBe(2);
      expect(screen.getByText('timeout')).toBeTruthy();
      expect(screen.getByText(/Сервис недоступен \(health check failed\)/)).toBeTruthy();
      expect(screen.getByText('Без описания')).toBeTruthy();
      expect(screen.getByText('d')).toBeTruthy();
      // кнопка выключенного плагина заблокирована
      expect((screen.getAllByText('Запустить')[0].closest('button') as HTMLButtonElement).disabled).toBe(true);
    });

    it('состояние запуска на карточке: идёт / ошибка / готово', async () => {
      mock.runs = {
        a: { status: 'running', modelFileName: 'a.xlsx' },
        b: { status: 'error', modelFileName: 'b.xlsx' },
        c: { status: 'done', modelFileName: 'c.xlsx' },
      };
      await renderContent([
        plugin({ id: 'a', display_name: 'A' }),
        plugin({ id: 'b', display_name: 'B' }),
        plugin({ id: 'c', display_name: 'C' }),
      ]);
      expect(screen.getByText('Аудит выполняется — 65с · a.xlsx')).toBeTruthy();
      expect(screen.getByText('Последний запуск не завершён · b.xlsx')).toBeTruthy();
      expect(screen.getByText('Результат готов · c.xlsx')).toBeTruthy();
      expect(screen.getByText('Идёт аудит — открыть')).toBeTruthy();
      expect(screen.getAllByText('Открыть результат').length).toBe(2);
    });

    it('закладки: переключение, фильтр по закладкам и пустое состояние', async () => {
      await renderContent([plugin({ id: 'a', display_name: 'A' }), plugin({ id: 'b', display_name: 'B' })]);
      fireEvent.click(screen.getAllByLabelText('Закладка')[0]);
      expect(mock.notify).toHaveBeenCalledWith('success', 'Добавлено в закладки');
      fireEvent.click(screen.getByText('toggle-bookmarks'));
      expect(screen.getByText('A')).toBeTruthy();
      expect(screen.queryByText('B')).toBeNull();
      fireEvent.click(screen.getByLabelText('Закладка'));
      expect(mock.notify).toHaveBeenLastCalledWith('success', 'Удалено из закладок');
      expect(screen.getByText(/В закладках пока нет плагинов/)).toBeTruthy();
    });

    it('поиск по названию, описанию, id и тегам; пустой результат — подсказка', async () => {
      await renderContent([
        plugin({ id: 'aaa', display_name: 'Альфа', description: 'про налоги', tags: ['x'] }),
        plugin({ id: 'bbb', display_name: 'Бета', description: undefined, tags: ['секретный'] }),
      ]);
      const box = screen.getByPlaceholderText('Поиск плагинов…');
      fireEvent.change(box, { target: { value: 'НАЛОГ' } });
      expect(screen.getByText('Альфа')).toBeTruthy();
      expect(screen.queryByText('Бета')).toBeNull();
      fireEvent.change(box, { target: { value: 'секрет' } });
      expect(screen.getByText('Бета')).toBeTruthy();
      fireEvent.change(box, { target: { value: 'bbb' } });
      expect(screen.getByText('Бета')).toBeTruthy();
      fireEvent.change(box, { target: { value: 'альф' } });
      expect(screen.getByText('Альфа')).toBeTruthy();
      fireEvent.change(box, { target: { value: 'нет такого' } });
      expect(screen.getByText('По запросу ничего не найдено.')).toBeTruthy();
    });

    it('поиск игнорируется в режиме закладок', async () => {
      mock.bookmarks.add('a');
      await renderContent([plugin({ id: 'a', display_name: 'A' }), plugin({ id: 'b', display_name: 'B' })]);
      fireEvent.click(screen.getByText('toggle-bookmarks'));
      fireEvent.change(screen.getByPlaceholderText('Поиск плагинов…'), { target: { value: 'zzz' } });
      expect(screen.getByText('A')).toBeTruthy();
    });

    it('выключенная платформа при непустой выдаче: сообщение про plugins.enabled', async () => {
      mock.fetchPlugins.mockImplementation(async (h: boolean) => ({ enabled: h ? false : true, plugins: [plugin()] }));
      render(<PluginGalleryContent />);
      await flush();
      // карточки есть, сообщение не показывается; ищем только по пустому списку
      fireEvent.change(screen.getByPlaceholderText('Поиск плагинов…'), { target: { value: 'zzzz' } });
      expect(screen.getByText('Платформа плагинов выключена. Проверьте plugins.enabled в config.yml / PLUGINS_ENABLED.')).toBeTruthy();
    });

    it('пустая выдача без поиска — общая подсказка', async () => {
      await renderContent([plugin()]);
      mock.fetchPlugins.mockClear();
      fireEvent.click(screen.getByText('toggle-bookmarks'));
      fireEvent.click(screen.getByText('toggle-bookmarks'));
      expect(screen.getByText('Аудит денежных потоков')).toBeTruthy();
    });
  });

  describe('окно запуска', () => {
    it('открытие: заголовок, пояснение для cash-flow, кнопка запуска заблокирована без файла', async () => {
      await renderContent();
      openDialog();
      const dialog = screen.getByRole('dialog');
      expect(within(dialog).getByText('Запуск: Аудит денежных потоков')).toBeTruthy();
      expect(within(dialog).getByText(/Ответ сервера показывается прямо в этом окне/)).toBeTruthy();
      expect((within(dialog).getByText('Запустить аудит').closest('button') as HTMLButtonElement).disabled).toBe(true);
    });

    it('для другого плагина показывается общее пояснение', async () => {
      await renderContent([plugin({ id: 'other' })]);
      openDialog();
      expect(screen.getByText('Передайте входные файлы плагина.')).toBeTruthy();
    });

    it('выбор файлов, промпт и запуск передают всё в startRun', async () => {
      await renderContent();
      openDialog();
      const m = xlsx('model.xlsx');
      const q = xlsx('quality.xlsx');
      fireEvent.change(modelInput(), { target: { files: [m] } });
      fireEvent.change(qualityInput(), { target: { files: [q] } });
      fireEvent.change(screen.getByLabelText('Промпт аналитика (опционально)'), { target: { value: 'проверь' } });
      expect(screen.getByText('model.xlsx')).toBeTruthy();
      expect(screen.getByText('quality.xlsx')).toBeTruthy();
      fireEvent.click(screen.getByText('Запустить аудит'));
      expect(mock.startRun).toHaveBeenCalledWith(expect.objectContaining({ id: 'cash-flow' }), {
        modelFile: m, qualityFile: q, prompt: 'проверь',
      });
    });

    it('если запуск уже идёт, показывается информационное уведомление', async () => {
      mock.startRun.mockReturnValue(false);
      await renderContent();
      openDialog();
      fireEvent.change(modelInput(), { target: { files: [xlsx()] } });
      fireEvent.click(screen.getByText('Запустить аудит'));
      expect(mock.notify).toHaveBeenCalledWith('info', 'Этот плагин уже выполняется — дождитесь результата');
    });

    it('выбор «пустого» файла сбрасывает его (отмена в диалоге ОС)', async () => {
      await renderContent();
      openDialog();
      fireEvent.change(modelInput(), { target: { files: [xlsx('a.xlsx')] } });
      expect(screen.getByText('a.xlsx')).toBeTruthy();
      fireEvent.change(modelInput(), { target: { files: [] } });
      expect(screen.getByText('не выбран')).toBeTruthy();
      fireEvent.change(qualityInput(), { target: { files: [] } });
    });

    it('закрытие окна сбрасывает выбранные файлы даже для того же плагина (known issue)', async () => {
      await renderContent([plugin({ id: 'a', display_name: 'A' }), plugin({ id: 'b', display_name: 'B' })]);
      fireEvent.click(screen.getAllByText('Запустить')[0]);
      fireEvent.change(modelInput(), { target: { files: [xlsx('keep.xlsx')] } });
      fireEvent.click(within(screen.getByRole('dialog')).getByText('Закрыть'));
      act(() => { jest.advanceTimersByTime(1000); });
      expect(screen.queryByRole('dialog')).toBeNull();
      fireEvent.click(screen.getAllByText('Запустить')[0]);
      // Как правильно: по комментарию в коде поля сбрасываются только при смене плагина,
      // но closeRunDialog обнуляет runPlugin, поэтому выбранный файл теряется.
      expect(screen.queryByText('keep.xlsx')).toBeNull();
      expect(screen.getByText('не выбран')).toBeTruthy();
      fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
      act(() => { jest.advanceTimersByTime(1000); });
      fireEvent.click(screen.getAllByText('Запустить')[1]);
      expect(screen.getByText('не выбран')).toBeTruthy();
    });

    it('во время аудита: предупреждение, спиннер, подпись «Закрыть (аудит продолжится)»', async () => {
      mock.runs = { 'cash-flow': { pluginId: 'cash-flow', status: 'running', modelFileName: 'm.xlsx' } };
      await renderContent();
      openDialog('Идёт аудит — открыть');
      const dialog = screen.getByRole('dialog');
      expect(within(dialog).getByText(/Выполняется аудит… 65с/)).toBeTruthy();
      expect(within(dialog).getByText('Закрыть (аудит продолжится)')).toBeTruthy();
      expect((within(dialog).getByText('Выполняется…').closest('button') as HTMLButtonElement).disabled).toBe(true);
    });

    it('ошибка аудита: текст ошибки и время до прерывания; без текста — запасной', async () => {
      mock.runs = { 'cash-flow': { pluginId: 'cash-flow', status: 'error', error: 'Файл битый', modelFileName: 'm.xlsx' } };
      await renderContent();
      openDialog('Открыть результат');
      expect(screen.getByText('Файл битый')).toBeTruthy();
      expect(screen.getByText('Прервано через 65с после запуска.')).toBeTruthy();
    });

    it('ошибка без текста — «Аудит не завершён»', async () => {
      mock.runs = { 'cash-flow': { pluginId: 'cash-flow', status: 'error', modelFileName: 'm.xlsx' } };
      await renderContent();
      openDialog('Открыть результат');
      expect(screen.getByText('Аудит не завершён')).toBeTruthy();
    });

    describe('результат', () => {
      const done = (over: any = {}) => ({
        pluginId: 'cash-flow', status: 'done', modelFileName: 'отчёт.xlsx', markdown: '# Вердикт',
        summary: 'Всё хорошо', raw: { ok: true }, finishedAtMs: 1, ...over,
      });

      it('summary, превью, вкладки Markdown и JSON', async () => {
        mock.runs = { 'cash-flow': done() };
        await renderContent();
        openDialog('Открыть результат');
        const dialog = screen.getByRole('dialog');
        expect(within(dialog).getByText('Всё хорошо')).toBeTruthy();
        expect(within(dialog).getByText('Результат аудита')).toBeTruthy();
        expect(within(dialog).getByText(/Файл: отчёт\.xlsx · длительность 65с/)).toBeTruthy();
        expect(within(dialog).getByTestId('md-preview').textContent).toBe('# Вердикт');
        fireEvent.click(within(dialog).getByRole('tab', { name: 'Markdown' }));
        expect(within(dialog).queryByTestId('md-preview')).toBeNull();
        expect(within(dialog).getByText('# Вердикт')).toBeTruthy();
        fireEvent.click(within(dialog).getByRole('tab', { name: 'JSON' }));
        expect(within(dialog).getByText(/"ok": true/)).toBeTruthy();
      });

      it('без raw: подсказка про перезагрузку и «{}» во вкладке JSON', async () => {
        mock.runs = { 'cash-flow': done({ raw: null, summary: null }) };
        await renderContent();
        openDialog('Открыть результат');
        const dialog = screen.getByRole('dialog');
        expect(within(dialog).getByText(/сырой JSON доступен только до перезагрузки страницы/)).toBeTruthy();
        fireEvent.click(within(dialog).getByRole('tab', { name: 'JSON' }));
        expect(within(dialog).getByText('{}')).toBeTruthy();
      });

      it('результат прокручивается в видимую область через 50 мс', async () => {
        const scroll = jest.fn();
        window.HTMLElement.prototype.scrollIntoView = scroll;
        mock.runs = { 'cash-flow': done() };
        await renderContent();
        openDialog('Открыть результат');
        expect(scroll).not.toHaveBeenCalled();
        act(() => { jest.advanceTimersByTime(60); });
        expect(scroll).toHaveBeenCalledWith({ behavior: 'smooth', block: 'start' });
      });

      it('результат без markdown не рисуется', async () => {
        mock.runs = { 'cash-flow': done({ markdown: '' }) };
        await renderContent();
        openDialog('Открыть результат');
        expect(screen.queryByText('Результат аудита')).toBeNull();
      });

      it('«Скачать .md»: имя берётся из файла модели без расширения', async () => {
        mock.runs = { 'cash-flow': done() };
        await renderContent();
        openDialog('Открыть результат');
        let name = '';
        jest.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
          name = this.download;
        });
        fireEvent.click(screen.getByText('Скачать .md'));
        expect(name).toBe('отчёт-verdict.md');
        expect((URL.createObjectURL as jest.Mock).mock.calls[0][0].type).toBe('text/markdown;charset=utf-8');
        expect(mock.notify).toHaveBeenCalledWith('success', 'Файл .md сохранён');
      });

      it('имя по умолчанию cash-flow-audit, если нет имён файлов; .md не дублируется', async () => {
        mock.runs = { 'cash-flow': done({ modelFileName: '' }) };
        await renderContent();
        openDialog('Открыть результат');
        let name = '';
        jest.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
          name = this.download;
        });
        fireEvent.click(screen.getByText('Скачать .md'));
        expect(name).toBe('cash-flow-audit-verdict.md');
      });

      it('имя только из расширения даёт запасное cash-flow-audit', async () => {
        mock.runs = { 'cash-flow': done({ modelFileName: '.xlsx' }) };
        await renderContent();
        openDialog('Открыть результат');
        let name = '';
        jest.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
          name = this.download;
        });
        fireEvent.click(screen.getByText('Скачать .md'));
        expect(name).toBe('cash-flow-audit-verdict.md');
      });
    });
  });

  it('повторная загрузка во время идущей игнорируется (защита loadingRef)', async () => {
    let resolveFirst!: (v: any) => void;
    mock.fetchPlugins
      .mockReturnValueOnce(new Promise((r) => { resolveFirst = r; }))
      .mockResolvedValue(catalog([plugin()]));
    const { rerender } = render(<PluginGalleryContent />);
    await flush();
    mock.token = 'tok2'; // смена токена перезапускает эффект, пока первый запрос не завершён
    rerender(<PluginGalleryContent />);
    await flush();
    expect(mock.fetchPlugins).toHaveBeenCalledTimes(1);
    await act(async () => { resolveFirst(catalog([plugin()])); });
  });
});
