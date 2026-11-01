import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom';
import ArtifactPanel from '@/components/artifacts/ArtifactPanel';
import type { ChatArtifact } from '@/types/artifacts';

const ctx: any = {};
const mockShowNotification = jest.fn();
const mockPreviewMounts = jest.fn();

jest.mock('@/contexts/ArtifactContext', () => ({
  useArtifactContext: () => ctx,
}));
jest.mock('@/contexts/AppContext', () => ({
  useAppActions: () => ({ showNotification: mockShowNotification }),
}));
jest.mock('@/components/artifacts/ArtifactPreview', () => {
  const R = require('react');
  return {
    __esModule: true,
    default: (p: any) => {
      R.useEffect(() => {
        mockPreviewMounts();
      }, []);
      return (
        <div data-testid="preview" data-streaming={String(p.isStreaming)}>
          {p.artifact.content}
        </div>
      );
    },
  };
});
jest.mock('@monaco-editor/react', () => ({
  __esModule: true,
  default: (p: any) => (
    <textarea
      data-testid="editor"
      data-language={p.language}
      value={p.value}
      onChange={(e) => p.onChange(e.target.value === '__undef__' ? undefined : e.target.value)}
    />
  ),
}));

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

function setCtx(over: Record<string, unknown> = {}) {
  Object.assign(ctx, {
    current: art(),
    isOpen: true,
    tab: 'preview',
    setTab: jest.fn(),
    closeArtifact: jest.fn(),
    updateArtifact: jest.fn(),
    openArtifact: jest.fn(),
    ...over,
  });
}

describe('ArtifactPanel', () => {
  beforeEach(() => {
    mockShowNotification.mockReset();
    mockPreviewMounts.mockReset();
    setCtx();
  });

  it('без текущего артефакта ничего не рендерит', () => {
    setCtx({ current: null });
    const { container } = render(<ArtifactPanel />);
    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByText('Просмотр')).toBeNull();
  });

  it('показывает название, тип и вкладку просмотра; закрытый артефакт без пометки «пишется»', () => {
    render(<ArtifactPanel />);
    expect(screen.getByText('Отчёт')).toBeInTheDocument();
    expect(screen.getByText('HTML')).toBeInTheDocument();
    expect(screen.getByTestId('preview')).toHaveTextContent('<p>x</p>');
    expect(screen.getByTestId('preview')).toHaveAttribute('data-streaming', 'false');
    expect(screen.queryByTestId('editor')).toBeNull();
  });

  it('незакрытый артефакт помечен «пишется…» и передаётся в превью как стрим', () => {
    setCtx({ current: art({ closed: false }) });
    render(<ArtifactPanel />);
    expect(screen.getByText(/пишется…/)).toBeInTheDocument();
    expect(screen.getByTestId('preview')).toHaveAttribute('data-streaming', 'true');
  });

  it('при isOpen=false панель (Drawer) не показывается', () => {
    setCtx({ isOpen: false });
    render(<ArtifactPanel />);
    expect(screen.queryByText('Отчёт')).toBeNull();
  });

  it('вкладка «Код»: редактор с языком типа и содержимым, превью скрыто', () => {
    setCtx({ tab: 'code', current: art({ type: 'application/vnd.mermaid', content: 'graph TD' }) });
    render(<ArtifactPanel />);
    const editor = screen.getByTestId('editor') as HTMLTextAreaElement;
    expect(editor.value).toBe('graph TD');
    expect(editor).toHaveAttribute('data-language', 'mermaid');
    expect(screen.queryByTestId('preview')).toBeNull();
  });

  it('редактирование кода вызывает updateArtifact с новым content', () => {
    setCtx({ tab: 'code' });
    render(<ArtifactPanel />);
    fireEvent.change(screen.getByTestId('editor'), { target: { value: 'новый код' } });
    expect(ctx.updateArtifact).toHaveBeenCalledWith(expect.objectContaining({ id: 'a1', content: 'новый код' }));
  });

  it('очистка редактора (value=undefined) сохраняется как пустая строка', () => {
    setCtx({ tab: 'code' });
    render(<ArtifactPanel />);
    fireEvent.change(screen.getByTestId('editor'), { target: { value: '__undef__' } });
    expect(ctx.updateArtifact).toHaveBeenCalledWith(expect.objectContaining({ content: '' }));
  });

  it('пустое содержимое артефакта показывается пустым редактором', () => {
    setCtx({ tab: 'code', current: art({ content: undefined as unknown as string }) });
    render(<ArtifactPanel />);
    expect((screen.getByTestId('editor') as HTMLTextAreaElement).value).toBe('');
  });

  it('переключение вкладок вызывает setTab', () => {
    render(<ArtifactPanel />);
    fireEvent.click(screen.getByRole('tab', { name: 'Код' }));
    expect(ctx.setTab).toHaveBeenCalledWith('code');
  });

  it('кнопка «Закрыть» и Escape вызывают closeArtifact', () => {
    render(<ArtifactPanel />);
    fireEvent.click(screen.getByLabelText('Закрыть'));
    expect(ctx.closeArtifact).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(screen.getByRole('presentation'), { key: 'Escape' });
    expect(ctx.closeArtifact.mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  it('«Обновить предпросмотр» перемонтирует превью', () => {
    render(<ArtifactPanel />);
    expect(mockPreviewMounts).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByLabelText('Обновить предпросмотр'));
    expect(mockPreviewMounts).toHaveBeenCalledTimes(2);
  });

  describe('копирование', () => {
    afterEach(() => {
      delete (navigator as any).clipboard;
    });

    it('успех: текст в буфер и уведомление «Скопировано»', async () => {
      const writeText = jest.fn().mockResolvedValue(undefined);
      Object.assign(navigator, { clipboard: { writeText } });
      render(<ArtifactPanel />);
      await act(async () => {
        fireEvent.click(screen.getByLabelText('Копировать'));
      });
      expect(writeText).toHaveBeenCalledWith('<p>x</p>');
      expect(mockShowNotification).toHaveBeenCalledWith('success', 'Скопировано');
    });

    it('сбой записи в буфер -> уведомление об ошибке', async () => {
      const writeText = jest.fn().mockRejectedValue(new Error('denied'));
      Object.assign(navigator, { clipboard: { writeText } });
      render(<ArtifactPanel />);
      await act(async () => {
        fireEvent.click(screen.getByLabelText('Копировать'));
      });
      expect(mockShowNotification).toHaveBeenCalledWith('error', 'Не удалось скопировать текст');
    });

    it('пустое содержимое копируется как пустая строка', async () => {
      const writeText = jest.fn().mockResolvedValue(undefined);
      Object.assign(navigator, { clipboard: { writeText } });
      setCtx({ current: art({ content: undefined as unknown as string }) });
      render(<ArtifactPanel />);
      await act(async () => {
        fireEvent.click(screen.getByLabelText('Копировать'));
      });
      expect(writeText).toHaveBeenCalledWith('');
    });
  });

  describe('скачивание', () => {
    let created: Blob[];
    let clickedDownloads: string[];
    beforeEach(() => {
      created = [];
      clickedDownloads = [];
      (URL as any).createObjectURL = jest.fn((b: Blob) => {
        created.push(b);
        return 'blob:fake';
      });
      (URL as any).revokeObjectURL = jest.fn();
      jest
        .spyOn(HTMLAnchorElement.prototype, 'click')
        .mockImplementation(function (this: HTMLAnchorElement) {
          clickedDownloads.push(this.download);
        });
    });
    afterEach(() => {
      jest.restoreAllMocks();
    });

    it('файл называется по identifier (небезопасные символы -> "_") с расширением типа', () => {
      render(<ArtifactPanel />);
      fireEvent.click(screen.getByLabelText('Скачать'));
      expect(clickedDownloads).toEqual(['my_report.html']);
      expect(created[0].type).toBe('text/plain;charset=utf-8');
      expect((URL as any).revokeObjectURL).toHaveBeenCalledWith('blob:fake');
    });

    it.each([
      ['image/svg+xml', 'svg'],
      ['text/markdown', 'md'],
      ['application/vnd.mermaid', 'mmd'],
      ['application/vnd.react', 'tsx'],
      ['application/x-unknown', 'txt'],
    ])('тип %s -> расширение .%s', (type, ext) => {
      setCtx({ current: art({ type, identifier: 'f' }) });
      render(<ArtifactPanel />);
      fireEvent.click(screen.getByLabelText('Скачать'));
      expect(clickedDownloads).toEqual([`f.${ext}`]);
    });

    it('без identifier имя «artifact»', () => {
      setCtx({ current: art({ identifier: '' }) });
      render(<ArtifactPanel />);
      fireEvent.click(screen.getByLabelText('Скачать'));
      expect(clickedDownloads).toEqual(['artifact.html']);
    });

    it('(known issue) кириллический identifier превращается в «_» и теряет читаемость', () => {
      // Правильное поведение: сохранять юникодные буквы (/[^\p{L}\p{N}._-]+/u); та же причина, что BUG-FE-13.
      setCtx({ current: art({ identifier: 'Схема продаж' }) });
      render(<ArtifactPanel />);
      fireEvent.click(screen.getByLabelText('Скачать'));
      expect(clickedDownloads).toEqual(['_.html']);
    });
  });
});
