import '@testing-library/jest-dom';
import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import InlineImageLightbox from '@/components/InlineImageLightbox';
import MessageMoreActionsMenu from '@/components/MessageMoreActionsMenu';

jest.mock('@/config/api', () => ({
  authFetch: (...a: unknown[]) => (require('@tests/unit/helpers/authFetchMock').authFetchMock as (...x: unknown[]) => unknown)(...a),
  getAuthFetchHeaders: () => ({ Authorization: 'Bearer T' }),
}));
jest.mock('@/components/AuthenticatedInlineImage', () => ({
  __esModule: true,
  default: ({ src, alt }: { src: string; alt: string }) => <img data-testid="lb-img" src={src} alt={alt} />,
}));

describe('InlineImageLightbox', () => {
  let fetchMock: jest.Mock;
  let anchors: Array<{ href: string; download: string; target: string; rel: string }>;

  beforeEach(() => {
    fetchMock = jest.fn();
    (global as any).fetch = fetchMock;
    anchors = [];
    (URL as any).createObjectURL = jest.fn(() => 'blob:dl');
    (URL as any).revokeObjectURL = jest.fn();
    jest.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      anchors.push({ href: this.getAttribute('href') || '', download: this.download, target: this.target, rel: this.rel });
    });
  });
  afterEach(() => jest.restoreAllMocks());

  const setup = (over: Record<string, unknown> = {}) => {
    const h = { onClose: jest.fn(), onEdit: jest.fn() };
    render(<InlineImageLightbox open src="http://minio/a.png" name="фото.png" {...h} {...(over as any)} />);
    return h;
  };

  it('закрытое окно ничего не рисует', () => {
    setup({ open: false });
    expect(screen.queryByTestId('lb-img')).toBeNull();
  });

  it('показывает изображение и кнопки; крестик и клик по фону закрывают, клик по картинке нет', () => {
    const h = setup();
    expect(screen.getByTestId('lb-img')).toHaveAttribute('alt', 'фото.png');
    fireEvent.click(screen.getByTestId('lb-img'));
    expect(h.onClose).not.toHaveBeenCalled();
    fireEvent.click(screen.getByLabelText('Закрыть просмотр изображения'));
    expect(h.onClose).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByTestId('lb-img').parentElement!.parentElement!);
    expect(h.onClose).toHaveBeenCalledTimes(2);
  });

  it('«Редактировать» виден только при onEdit и showEdit', () => {
    const h = setup();
    fireEvent.click(screen.getByText('Редактировать'));
    expect(h.onEdit).toHaveBeenCalledTimes(1);
    expect(h.onClose).not.toHaveBeenCalled();
  });

  it('без onEdit или с showEdit=false кнопки редактирования нет', () => {
    const { unmount } = render(<InlineImageLightbox open src="blob:x" name="a" onClose={jest.fn()} />);
    expect(screen.queryByText('Редактировать')).toBeNull();
    unmount();
    render(<InlineImageLightbox open src="blob:x" name="a" onClose={jest.fn()} onEdit={jest.fn()} showEdit={false} />);
    expect(screen.queryByText('Редактировать')).toBeNull();
  });

  it('скачивание удалённого файла идёт с токеном и через blob-ссылку', async () => {
    fetchMock.mockResolvedValue({ ok: true, blob: async () => new Blob(['x']) });
    setup();
    await act(async () => {
      fireEvent.click(screen.getByText('Скачать'));
    });
    expect(fetchMock).toHaveBeenCalledWith('http://minio/a.png', { headers: { Authorization: 'Bearer T' } });
    expect(anchors).toEqual([{ href: 'blob:dl', download: 'фото.png', target: '', rel: '' }]);
    expect((URL as any).revokeObjectURL).toHaveBeenCalledWith('blob:dl');
  });

  it('data:/blob: источники скачиваются без заголовков авторизации', async () => {
    fetchMock.mockResolvedValue({ ok: true, blob: async () => new Blob(['x']) });
    setup({ src: 'data:image/png;base64,AA' });
    await act(async () => {
      fireEvent.click(screen.getByText('Скачать'));
    });
    expect(fetchMock).toHaveBeenCalledWith('data:image/png;base64,AA');
  });

  it('при ошибке загрузки скачивание открывает прямую ссылку в новой вкладке', async () => {
    fetchMock.mockResolvedValue({ ok: false, status: 403 });
    setup();
    await act(async () => {
      fireEvent.click(screen.getByText('Скачать'));
    });
    expect(anchors).toEqual([
      { href: 'http://minio/a.png', download: 'фото.png', target: '_blank', rel: 'noopener noreferrer' },
    ]);
    fetchMock.mockRejectedValue(new Error('net'));
    await act(async () => {
      fireEvent.click(screen.getByText('Скачать'));
    });
    expect(anchors).toHaveLength(2);
  });

  it('пустое имя файла заменяется на image.<расширение>', async () => {
    fetchMock.mockRejectedValue(new Error('net'));
    setup({ name: '' });
    await act(async () => {
      fireEvent.click(screen.getByText('Скачать'));
    });
    expect(anchors[0].download).toBe('image.png');
  });
});

describe('MessageMoreActionsMenu', () => {
  const setup = (over: Record<string, unknown> = {}) => {
    const h = {
      onEdit: jest.fn(),
      onReadAloud: jest.fn(),
      onBranch: jest.fn(),
      onExport: jest.fn(),
    };
    const utils = render(<MessageMoreActionsMenu isDarkMode={false} {...h} {...(over as any)} />);
    return { ...h, ...utils };
  };
  const openMenu = () => fireEvent.click(screen.getByRole('button', { name: 'Больше действий' }));

  it('кнопка открывает меню с базовыми пунктами', () => {
    setup();
    expect(screen.queryByText('Редактировать')).toBeNull();
    openMenu();
    expect(screen.getByText('Прочесть вслух')).toBeInTheDocument();
    expect(screen.getByText('Редактировать')).toBeInTheDocument();
    expect(screen.queryByText('Экспорт')).toBeNull();
    expect(screen.queryByText('Ветка в новом чате')).toBeNull();
  });

  it('disabled блокирует кнопку; compact и iconSx принимаются', () => {
    const { rerender } = setup({ disabled: true, compact: true, isDarkMode: true });
    expect(screen.getByRole('button', { name: 'Больше действий' })).toBeDisabled();
    rerender(<MessageMoreActionsMenu isDarkMode onEdit={jest.fn()} onReadAloud={jest.fn()} iconSx={{ color: 'red' }} />);
    expect(screen.getByRole('button', { name: 'Больше действий' })).not.toBeDisabled();
  });

  it('«Редактировать» и «Прочесть вслух» вызывают обработчики и закрывают меню', async () => {
    const h = setup();
    openMenu();
    fireEvent.click(screen.getByText('Редактировать'));
    expect(h.onEdit).toHaveBeenCalledTimes(1);
    await act(async () => {
      await new Promise((r) => setTimeout(r, 400));
    });
    expect(screen.queryByText('Прочесть вслух')).toBeNull();
    openMenu();
    fireEvent.click(screen.getByText('Прочесть вслух'));
    expect(h.onReadAloud).toHaveBeenCalledTimes(1);
  });

  it('во время озвучивания пункт «Прочесть вслух» неактивен', () => {
    const h = setup({ isSpeaking: true });
    openMenu();
    fireEvent.click(screen.getByText('Прочесть вслух'));
    expect(h.onReadAloud).not.toHaveBeenCalled();
  });

  it('ветка: пункт показывается по showBranch, блокируется branchDisabled', () => {
    const h = setup({ showBranch: true });
    openMenu();
    fireEvent.click(screen.getByText('Ветка в новом чате'));
    expect(h.onBranch).toHaveBeenCalledTimes(1);
  });

  it('ветка заблокирована: клик не вызывает onBranch; без onBranch клик безопасен', () => {
    const h = setup({ showBranch: true, branchDisabled: true });
    openMenu();
    fireEvent.click(screen.getByText('Ветка в новом чате'));
    expect(h.onBranch).not.toHaveBeenCalled();
  });

  it('ветка без обработчика не падает', () => {
    render(<MessageMoreActionsMenu isDarkMode onEdit={jest.fn()} onReadAloud={jest.fn()} showBranch />);
    fireEvent.click(screen.getByRole('button', { name: 'Больше действий' }));
    expect(() => fireEvent.click(screen.getByText('Ветка в новом чате'))).not.toThrow();
  });

  it('экспорт: подменю по наведению, выбор формата вызывает onExport и закрывает меню', () => {
    const h = setup({ showExport: true });
    openMenu();
    expect(screen.queryByText('PDF')).toBeNull();
    fireEvent.mouseEnter(screen.getByText('Экспорт').parentElement!);
    fireEvent.click(screen.getByText('PDF'));
    expect(h.onExport).toHaveBeenCalledWith('pdf');
  });

  it('экспорт в Word и закрытие подменю при уходе на другие пункты и мыши', () => {
    const h = setup({ showExport: true });
    openMenu();
    fireEvent.mouseEnter(screen.getByText('Экспорт').parentElement!);
    expect(screen.getByText('Word')).toBeInTheDocument();
    fireEvent.mouseEnter(screen.getByText('Прочесть вслух').parentElement!);
    expect(screen.queryByText('Word')).toBeNull();
    fireEvent.mouseEnter(screen.getByText('Экспорт').parentElement!);
    fireEvent.mouseEnter(screen.getByText('Редактировать').parentElement!);
    expect(screen.queryByText('Word')).toBeNull();
    fireEvent.mouseEnter(screen.getByText('Экспорт').parentElement!);
    fireEvent.mouseLeave(screen.getByText('Word').closest('.MuiPopover-paper')!.firstChild as Element);
    expect(screen.queryByText('Word')).toBeNull();
    fireEvent.mouseEnter(screen.getByText('Экспорт').parentElement!);
    fireEvent.click(screen.getByText('Word'));
    expect(h.onExport).toHaveBeenCalledWith('word');
  });

  it('при showBranch подменю экспорта закрывается и на пункте ветки', () => {
    setup({ showExport: true, showBranch: true });
    openMenu();
    fireEvent.mouseEnter(screen.getByText('Экспорт').parentElement!);
    fireEvent.mouseEnter(screen.getByText('Ветка в новом чате').parentElement!);
    expect(screen.queryByText('Word')).toBeNull();
  });

  it('экспорт отключён или без onExport: подменю не открывается', () => {
    setup({ showExport: true, exportDisabled: true });
    openMenu();
    fireEvent.mouseEnter(screen.getByText('Экспорт').parentElement!);
    expect(screen.queryByText('PDF')).toBeNull();
  });

  it('без onExport подменю тоже не открывается', () => {
    render(<MessageMoreActionsMenu isDarkMode onEdit={jest.fn()} onReadAloud={jest.fn()} showExport />);
    fireEvent.click(screen.getByRole('button', { name: 'Больше действий' }));
    fireEvent.mouseEnter(screen.getByText('Экспорт').parentElement!);
    expect(screen.queryByText('PDF')).toBeNull();
  });

  it('Escape закрывает меню', async () => {
    setup();
    openMenu();
    fireEvent.keyDown(document.querySelector('.MuiPopover-root')!, { key: 'Escape' });
    await act(async () => {
      await new Promise((r) => setTimeout(r, 400));
    });
    expect(screen.queryByText('Редактировать')).toBeNull();
  });
});
