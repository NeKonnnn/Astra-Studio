import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';
import ArtifactCard from '@/components/artifacts/ArtifactCard';
import type { ChatArtifact } from '@/types/artifacts';

const mockOpenArtifactViewer = jest.fn();
const mockExportPng = jest.fn();
const mockExportJpg = jest.fn();
const mockOpenPresentationViewer = jest.fn();

jest.mock('@/utils/artifactViewer', () => ({
  ...jest.requireActual('@/utils/artifactViewer'),
  openArtifactViewer: (...a: unknown[]) => mockOpenArtifactViewer(...a),
}));
jest.mock('@/utils/artifactDownload', () => ({
  exportArtifactPreviewAsPng: (...a: unknown[]) => mockExportPng(...a),
  exportArtifactPreviewAsJpg: (...a: unknown[]) => mockExportJpg(...a),
}));
jest.mock('@/utils/presentationViewer', () => ({
  ...jest.requireActual('@/utils/presentationViewer'),
  openPresentationViewer: (...a: unknown[]) => mockOpenPresentationViewer(...a),
}));
jest.mock('@/components/artifacts/ArtifactPreview', () => ({
  __esModule: true,
  default: (p: any) => (
    <div data-testid="preview" data-streaming={String(Boolean(p.isStreaming))}>
      {p.artifact.content}
    </div>
  ),
}));
jest.mock('@monaco-editor/react', () => ({
  __esModule: true,
  default: (p: any) => {
    const R = require('react');
    R.useEffect(() => {
      p.onMount?.({ layout: (globalThis as any).__editorLayout });
    }, []);
    return (
      <textarea
        data-testid="editor"
        data-language={p.language}
        data-path={p.path}
        data-height={String(p.height)}
        value={p.value}
        onChange={(e) => p.onChange(e.target.value === '__undef__' ? undefined : e.target.value)}
      />
    );
  },
}));

type IOCallback = (entries: Array<{ isIntersecting: boolean }>) => void;
let ioCallbacks: IOCallback[] = [];
const ioDisconnect = jest.fn();

function art(over: Partial<ChatArtifact> = {}): ChatArtifact {
  return {
    id: 'a1',
    identifier: 'my report',
    type: 'text/html',
    title: 'Отчёт',
    content: '<p>x</p>',
    closed: true,
    ...over,
  };
}

function scrollIntoView() {
  // IntersectionObserver появляется в поле зрения
  act(() => {
    ioCallbacks.forEach((cb) => cb([{ isIntersecting: true }]));
  });
}

describe('ArtifactCard', () => {
  beforeEach(() => {
    ioCallbacks = [];
    ioDisconnect.mockReset();
    mockOpenArtifactViewer.mockReset().mockResolvedValue(undefined);
    mockExportPng.mockReset().mockResolvedValue(undefined);
    mockExportJpg.mockReset().mockResolvedValue(undefined);
    mockOpenPresentationViewer.mockReset();
    (globalThis as any).__editorLayout = jest.fn();
    (window as any).IntersectionObserver = class {
      constructor(cb: IOCallback) {
        ioCallbacks.push(cb);
      }
      observe() {}
      disconnect() {
        ioDisconnect();
      }
    };
    jest.spyOn(window, 'requestAnimationFrame').mockImplementation((cb: FrameRequestCallback) => {
      cb(0);
      return 0;
    });
  });
  afterEach(() => {
    jest.restoreAllMocks();
    delete (navigator as any).clipboard;
  });

  describe('предпросмотр и видимость', () => {
    it('вне экрана превью не монтируется до пересечения с viewport, потом показывается', () => {
      render(<ArtifactCard artifact={art()} />);
      expect(screen.queryByTestId('preview')).toBeNull();
      scrollIntoView();
      expect(screen.getByTestId('preview')).toHaveTextContent('<p>x</p>');
    });

    it('уход из viewport снова убирает превью, повторный callback без смены состояния ничего не делает', () => {
      render(<ArtifactCard artifact={art()} />);
      scrollIntoView();
      scrollIntoView();
      act(() => ioCallbacks.forEach((cb) => cb([{ isIntersecting: false }])));
      expect(screen.queryByTestId('preview')).toBeNull();
    });

    it('во время генерации превью показывается сразу, со статусом «генерация…» и без кнопок действий', () => {
      render(<ArtifactCard artifact={art({ closed: false })} isStreaming />);
      expect(screen.getByTestId('preview')).toHaveAttribute('data-streaming', 'true');
      expect(screen.getAllByText('Отчёт · генерация…')).toHaveLength(2); // база + блик (aria-hidden)
      expect(screen.queryByLabelText('Скачать артефакт')).toBeNull();
      expect(screen.queryByLabelText('Открыть в новой вкладке')).toBeNull();
      expect(screen.queryByLabelText('Показать HTML')).toBeNull();
      expect(screen.getByRole('progressbar')).toBeInTheDocument();
    });

    it('без заголовка статус строится из названия типа', () => {
      render(<ArtifactCard artifact={art({ title: '', closed: false, type: 'application/vnd.mermaid' })} isStreaming />);
      expect(screen.getAllByText('Mermaid · генерация…')).toHaveLength(2);
    });

    it('завершённый артефакт показывает только заголовок', () => {
      render(<ArtifactCard artifact={art()} />);
      expect(screen.getByText('Отчёт')).toBeInTheDocument();
      expect(screen.queryByRole('progressbar')).toBeNull();
    });

    it('при стриме превью берёт актуальный content артефакта, после — локальный (редактируемый)', () => {
      const { rerender } = render(
        <ArtifactCard artifact={art({ content: 'a', closed: false })} isStreaming />,
      );
      expect(screen.getByTestId('preview')).toHaveTextContent('a');
      rerender(<ArtifactCard artifact={art({ content: 'ab', closed: false })} isStreaming />);
      expect(screen.getByTestId('preview')).toHaveTextContent('ab');
      rerender(<ArtifactCard artifact={art({ content: 'abc', closed: true })} isStreaming={false} />);
      scrollIntoView();
      expect(screen.getByTestId('preview')).toHaveTextContent('abc');
    });
  });

  describe('исходный код', () => {
    function openSource(artifact = art()) {
      render(<ArtifactCard artifact={artifact} />);
      fireEvent.click(screen.getByLabelText(/Показать/));
    }

    it('кнопка показывает/скрывает редактор; подпись по типу артефакта', async () => {
      render(<ArtifactCard artifact={art({ type: 'image/svg+xml' })} />);
      expect(screen.queryByTestId('editor')).toBeNull();
      fireEvent.click(screen.getByLabelText('Показать SVG'));
      expect(screen.getByTestId('editor')).toBeInTheDocument();
      fireEvent.click(screen.getByLabelText('Скрыть SVG'));
      await waitFor(() => expect(screen.queryByTestId('editor')).toBeNull());
    });

    it('неизвестный тип -> подпись «код»', () => {
      render(<ArtifactCard artifact={art({ type: 'application/x-foo' })} />);
      expect(screen.getByLabelText('Показать код')).toBeInTheDocument();
    });

    it('редактор получает язык, виртуальный путь и содержимое; onMount вызывает layout дважды', () => {
      openSource(art({ id: 'id9', type: 'text/markdown', content: '# a' }));
      const ed = screen.getByTestId('editor') as HTMLTextAreaElement;
      expect(ed.value).toBe('# a');
      expect(ed).toHaveAttribute('data-language', 'markdown');
      expect(ed).toHaveAttribute('data-path', 'artifact://id9.md');
      expect((globalThis as any).__editorLayout).toHaveBeenCalledTimes(2);
    });

    it('путь редактора без id использует identifier, а затем «src»', () => {
      openSource(art({ id: '', identifier: 'ident', type: 'image/svg+xml' }));
      expect(screen.getByTestId('editor')).toHaveAttribute('data-path', 'artifact://ident.svg');
    });

    it('путь без id и identifier -> src.txt для неизвестного типа', () => {
      openSource(art({ id: '', identifier: '', type: 'application/x-foo' }));
      expect(screen.getByTestId('editor')).toHaveAttribute('data-path', 'artifact://src.txt');
    });

    it.each([
      ['application/vnd.mermaid', 'mmd'],
      ['application/vnd.react', 'tsx'],
    ])('расширение виртуального пути для %s -> .%s', (type, ext) => {
      openSource(art({ id: 'z', type }));
      expect(screen.getByTestId('editor').getAttribute('data-path')).toBe(`artifact://z.${ext}`);
    });

    it('высота редактора растёт с числом строк в пределах [200..480]', () => {
      openSource(art({ content: 'a' }));
      expect(screen.getByTestId('editor')).toHaveAttribute('data-height', '200');
    });

    it('длинный код ограничивает высоту редактора 22 строками (502 -> 480)', () => {
      openSource(art({ content: Array.from({ length: 100 }, () => 'x').join('\n') }));
      expect(screen.getByTestId('editor')).toHaveAttribute('data-height', '480');
    });

    it('средняя длина: 10 строк -> 10*22+18=238', () => {
      openSource(art({ content: Array.from({ length: 10 }, () => 'x').join('\n') }));
      expect(screen.getByTestId('editor')).toHaveAttribute('data-height', '238');
    });

    it('правка в редакторе меняет локальное содержимое и превью; пустое значение -> пустая строка', () => {
      render(<ArtifactCard artifact={art()} />);
      scrollIntoView();
      fireEvent.click(screen.getByLabelText('Показать HTML'));
      fireEvent.change(screen.getByTestId('editor'), { target: { value: '<b>новый</b>' } });
      expect(screen.getByTestId('preview')).toHaveTextContent('<b>новый</b>');
      fireEvent.change(screen.getByTestId('editor'), { target: { value: '__undef__' } });
      expect((screen.getByTestId('editor') as HTMLTextAreaElement).value).toBe('');
    });

    it('смена content артефакта снаружи (не стрим) сбрасывает локальные правки', () => {
      const { rerender } = render(<ArtifactCard artifact={art({ content: 'v1' })} />);
      fireEvent.click(screen.getByLabelText('Показать HTML'));
      fireEvent.change(screen.getByTestId('editor'), { target: { value: 'правка' } });
      rerender(<ArtifactCard artifact={art({ content: 'v2' })} />);
      expect((screen.getByTestId('editor') as HTMLTextAreaElement).value).toBe('v2');
    });

    it('копирование кода: текст в буфере, иконка-галочка на 1.6 с, затем возврат', async () => {
      jest.useFakeTimers();
      const writeText = jest.fn().mockResolvedValue(undefined);
      Object.assign(navigator, { clipboard: { writeText } });
      openSource(art({ content: 'код' }));
      await act(async () => {
        fireEvent.click(screen.getByLabelText('Копировать код'));
      });
      expect(writeText).toHaveBeenCalledWith('код');
      expect(screen.getByLabelText('✓ Скопировано!')).toBeInTheDocument();
      act(() => {
        jest.advanceTimersByTime(1700);
      });
      expect(screen.getByLabelText('Копировать код')).toBeInTheDocument();
      jest.useRealTimers();
    });

    it('сбой записи в буфер молча игнорируется', async () => {
      const writeText = jest.fn().mockRejectedValue(new Error('denied'));
      Object.assign(navigator, { clipboard: { writeText } });
      openSource();
      await act(async () => {
        fireEvent.click(screen.getByLabelText('Копировать код'));
      });
      expect(writeText).toHaveBeenCalled();
      expect(screen.getByLabelText('Копировать код')).toBeInTheDocument();
    });

    it('скачивание файла: имя из identifier, расширение по типу, Blob с текущим кодом', () => {
      const blobs: Blob[] = [];
      const names: string[] = [];
      (URL as any).createObjectURL = jest.fn((b: Blob) => {
        blobs.push(b);
        return 'blob:x';
      });
      (URL as any).revokeObjectURL = jest.fn();
      jest.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
        names.push(this.download);
      });
      openSource(art({ identifier: 'my report', type: 'text/html' }));
      fireEvent.click(screen.getByLabelText('Скачать файл'));
      expect(names).toEqual(['my_report.html']);
      expect(blobs[0].type).toBe('text/plain;charset=utf-8');
      expect((URL as any).revokeObjectURL).toHaveBeenCalledWith('blob:x');
    });

    it('скачивание без identifier -> «artifact.txt» для неизвестного типа', () => {
      const names: string[] = [];
      (URL as any).createObjectURL = jest.fn(() => 'blob:x');
      (URL as any).revokeObjectURL = jest.fn();
      jest.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
        names.push(this.download);
      });
      openSource(art({ identifier: '', type: 'application/x-foo' }));
      fireEvent.click(screen.getByLabelText('Скачать файл'));
      expect(names).toEqual(['artifact.txt']);
    });

    it('(known issue) кириллический identifier в имени скачиваемого файла сводится к «_»', () => {
      // Правильное поведение: сохранять юникодные буквы в имени файла (как BUG-FE-13).
      const names: string[] = [];
      (URL as any).createObjectURL = jest.fn(() => 'blob:x');
      (URL as any).revokeObjectURL = jest.fn();
      jest.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
        names.push(this.download);
      });
      openSource(art({ identifier: 'Схема', type: 'text/markdown' }));
      fireEvent.click(screen.getByLabelText('Скачать файл'));
      expect(names).toEqual(['_.md']);
    });
  });

  describe('экспорт PNG/JPG', () => {
    function openMenu(artifact = art()) {
      render(<ArtifactCard artifact={artifact} />);
      fireEvent.click(screen.getByLabelText('Скачать артефакт'));
    }

    it('PNG: вызывает экспорт с корнем превью и безопасным именем, меню закрывается', async () => {
      openMenu(art({ identifier: 'my report' }));
      fireEvent.click(screen.getByText('PNG'));
      expect(mockExportPng).toHaveBeenCalledWith({
        previewRoot: expect.any(HTMLElement),
        fileNameBase: 'my_report',
      });
      await waitFor(() => expect(screen.queryByText('PNG')).toBeNull());
    });

    it('JPG: вызывает экспорт JPG', () => {
      openMenu();
      fireEvent.click(screen.getByText('JPG'));
      expect(mockExportJpg).toHaveBeenCalledWith(
        expect.objectContaining({ fileNameBase: 'my_report' }),
      );
    });

    it('имя берётся из title, когда identifier пуст; иначе «artifact»', () => {
      openMenu(art({ identifier: '', title: 'Мой title.v1' }));
      fireEvent.click(screen.getByText('PNG'));
      // «Мой » (кириллица+пробел) схлопывается в один "_" (\w только ASCII), ASCII и точка сохраняются
      expect(mockExportPng.mock.calls[0][0].fileNameBase).toBe('_title.v1');
    });

    it('без identifier и title имя «artifact»', () => {
      openMenu(art({ identifier: '', title: '' }));
      fireEvent.click(screen.getByText('JPG'));
      expect(mockExportJpg.mock.calls[0][0].fileNameBase).toBe('artifact');
    });

    it('ошибка экспорта PNG показывается alert с её сообщением', async () => {
      const alertSpy = jest.spyOn(window, 'alert').mockImplementation(() => {});
      mockExportPng.mockRejectedValue(new Error('canvas tainted'));
      openMenu();
      fireEvent.click(screen.getByText('PNG'));
      await waitFor(() => expect(alertSpy).toHaveBeenCalledWith('canvas tainted'));
    });

    it('не-Error при экспорте PNG/JPG -> стандартные сообщения', async () => {
      const alertSpy = jest.spyOn(window, 'alert').mockImplementation(() => {});
      mockExportPng.mockRejectedValue('oops');
      mockExportJpg.mockRejectedValue(42);
      openMenu();
      fireEvent.click(screen.getByText('PNG'));
      await waitFor(() => expect(alertSpy).toHaveBeenCalledWith('Не удалось скачать PNG.'));
      fireEvent.click(screen.getByLabelText('Скачать артефакт'));
      fireEvent.click(screen.getByText('JPG'));
      await waitFor(() => expect(alertSpy).toHaveBeenCalledWith('Не удалось скачать JPG.'));
    });

    it('ошибка экспорта JPG показывается alert с её сообщением', async () => {
      const alertSpy = jest.spyOn(window, 'alert').mockImplementation(() => {});
      mockExportJpg.mockRejectedValue(new Error('jpg fail'));
      openMenu();
      fireEvent.click(screen.getByText('JPG'));
      await waitFor(() => expect(alertSpy).toHaveBeenCalledWith('jpg fail'));
    });

    it('закрытие Popover по Escape убирает меню', async () => {
      openMenu();
      expect(screen.getByText('PNG')).toBeInTheDocument();
      fireEvent.keyDown(screen.getByText('PNG'), { key: 'Escape' });
      await waitFor(() => expect(screen.queryByText('PNG')).toBeNull());
    });
  });

  describe('открытие в новой вкладке', () => {
    it('обычный артефакт открывается через openArtifactViewer с типом, кодом и заголовком', async () => {
      render(<ArtifactCard artifact={art({ title: 'Т', content: 'код' })} />);
      fireEvent.click(screen.getByLabelText('Открыть в новой вкладке'));
      await waitFor(() => expect(mockOpenArtifactViewer).toHaveBeenCalledTimes(1));
      expect(mockOpenArtifactViewer).toHaveBeenCalledWith({
        type: 'text/html',
        content: 'код',
        title: 'Т',
        previewRoot: expect.any(HTMLElement),
      });
      expect(mockOpenPresentationViewer).not.toHaveBeenCalled();
    });

    it('без заголовка название берётся из типа', async () => {
      render(<ArtifactCard artifact={art({ title: '', type: 'application/vnd.mermaid' })} />);
      fireEvent.click(screen.getByLabelText('Открыть в новой вкладке'));
      await waitFor(() => expect(mockOpenArtifactViewer).toHaveBeenCalled());
      expect(mockOpenArtifactViewer.mock.calls[0][0].title).toBe('Mermaid');
    });

    it('HTML-презентация (.slide) открывается своим viewer, а не generic', async () => {
      const deck = '<div class="slide"><h1>Заголовок презентации</h1></div><div class="slide">x</div>';
      render(<ArtifactCard artifact={art({ content: deck })} />);
      fireEvent.click(screen.getByLabelText('Открыть в новой вкладке'));
      await waitFor(() => expect(mockOpenPresentationViewer).toHaveBeenCalledWith(deck));
      expect(mockOpenArtifactViewer).not.toHaveBeenCalled();
    });

    it('ошибка открытия viewer логируется и не пробрасывается', async () => {
      const err = jest.spyOn(console, 'error').mockImplementation(() => {});
      mockOpenArtifactViewer.mockRejectedValue(new Error('popup blocked'));
      render(<ArtifactCard artifact={art()} />);
      fireEvent.click(screen.getByLabelText('Открыть в новой вкладке'));
      await waitFor(() => expect(err).toHaveBeenCalledWith('Failed to open artifact viewer:', expect.any(Error)));
    });
  });
});
