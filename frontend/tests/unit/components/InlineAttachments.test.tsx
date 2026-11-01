import '@testing-library/jest-dom';
import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import InlineDocAttachmentChip, {
  InlineAttachUploadSpinner,
  InlineDocThumb,
  InlineDocUploadingThumb,
  formatInlineAttachmentFileName,
  getInlineDocChipCloseSx,
  getInlineDocChipSx,
  getInlineDocKind,
  getInlineDocThumbColor,
} from '@/components/InlineDocAttachmentChip';
import InlineAttachmentsList from '@/components/InlineAttachmentsList';
import AuthenticatedInlineImage from '@/components/AuthenticatedInlineImage';

let mockToken: string | null = 'tok';
jest.mock('@/contexts/AuthContext', () => ({ useAuth: () => ({ token: mockToken }) }));
// memo: AuthenticatedInlineImage грузит через authFetch (JWT + cookie client binding).
jest.mock('@/config/api', () => ({
  authFetch: (...a: unknown[]) => (require('@tests/unit/helpers/authFetchMock').authFetchMock as (...x: unknown[]) => unknown)(...a),
  getApiUrl: (p: string) => p,
  getAuthFetchHeaders: () => ({ Authorization: 'Bearer tok' }),
}));

describe('InlineDocAttachmentChip: чистые функции', () => {
  it('getInlineDocKind определяет тип по расширению без учёта регистра', () => {
    expect(getInlineDocKind('A.PDF')).toBe('pdf');
    expect(getInlineDocKind('a.doc')).toBe('word');
    expect(getInlineDocKind('a.DOCX')).toBe('word');
    expect(getInlineDocKind('a.xls')).toBe('excel');
    expect(getInlineDocKind('a.xlsx')).toBe('excel');
    expect(getInlineDocKind('a.txt')).toBe('txt');
    expect(getInlineDocKind('a.xlsm')).toBe('other');
    expect(getInlineDocKind('noext')).toBe('other');
  });

  it('getInlineDocThumbColor: цвета по типам и тёмная тема для прочих', () => {
    expect(getInlineDocThumbColor('pdf', false)).toBe('#d94a3a');
    expect(getInlineDocThumbColor('word', true)).toBe('#2b579a');
    expect(getInlineDocThumbColor('excel', false)).toBe('#217346');
    expect(getInlineDocThumbColor('txt', false)).toBe('#00a4ef');
    expect(getInlineDocThumbColor('other', true)).toBe('#546e7a');
    expect(getInlineDocThumbColor('other', false)).toBe('#78909c');
  });

  it('formatInlineAttachmentFileName: короткие имена без изменений, длинные с сохранением расширения', () => {
    expect(formatInlineAttachmentFileName('short.pdf')).toBe('short.pdf');
    const out = formatInlineAttachmentFileName('очень-длинное-название-документа-для-проверки.docx');
    expect(out.length).toBe(30);
    expect(out.endsWith(' …' + '.docx')).toBe(true);
    expect(formatInlineAttachmentFileName('x'.repeat(50))).toBe('x'.repeat(29) + '…');
  });

  it('очень длинное расширение обрезает имя целиком', () => {
    const out = formatInlineAttachmentFileName('a.' + 'e'.repeat(40), 10);
    expect(out).toBe('a.eeeeeee…');
  });

  it('стили чипа зависят от темы и режима сообщения', () => {
    expect(getInlineDocChipSx(true, true).bgcolor).not.toBe(getInlineDocChipSx(true, false).bgcolor);
    expect(getInlineDocChipSx(false, false).bgcolor).toBe('rgba(0, 0, 0, 0.05)');
    expect(getInlineDocChipSx(true, false).border).toContain('rgba(255, 255, 255, 0.12)');
    expect(getInlineDocChipSx(false, true).border).toContain('rgba(255, 255, 255, 0.2)');
    expect(getInlineDocChipCloseSx(false, true).bgcolor).toBe('rgba(0, 0, 0, 0.28)');
    expect(getInlineDocChipCloseSx(true, false).bgcolor).toBe('rgba(0, 0, 0, 0.38)');
    expect(getInlineDocChipCloseSx(false, false).bgcolor).toBe('rgba(0, 0, 0, 0.12)');
    expect(getInlineDocChipCloseSx(false, false)['&:hover'].color).toBe('#333');
    expect(getInlineDocChipCloseSx(true, false)['&:hover'].color).toBe('white');
  });
});

describe('InlineDocThumb и спиннер', () => {
  it('символика по типу файла', () => {
    const { rerender, container } = render(<InlineDocThumb filename="a.pdf" />);
    expect(container.querySelector('[data-testid="PictureAsPdfIcon"]')).toBeTruthy();
    rerender(<InlineDocThumb filename="a.docx" />);
    expect(screen.getByText('W')).toBeInTheDocument();
    rerender(<InlineDocThumb filename="a.xlsx" />);
    expect(screen.getByText('X')).toBeInTheDocument();
    rerender(<InlineDocThumb filename="a.txt" />);
    expect(screen.getByText('TXT')).toBeInTheDocument();
    rerender(<InlineDocThumb filename="a.bin" isDarkMode />);
    expect(container.querySelector('[data-testid="DescriptionIcon"]')).toBeTruthy();
  });

  it('hideSymbol скрывает символику', () => {
    const { container } = render(<InlineDocThumb filename="a.docx" hideSymbol />);
    expect(screen.queryByText('W')).toBeNull();
    expect(container.querySelector('[aria-hidden="true"]')).toBeTruthy();
  });

  it('спиннер загрузки: размер и подпись; загружаемая миниатюра содержит спиннер', () => {
    const { rerender } = render(<InlineAttachUploadSpinner size={30} />);
    const sp = screen.getByRole('progressbar', { name: 'Загрузка файла' });
    expect(getComputedStyle(sp).width).toBe('30px');
    expect(sp.querySelectorAll('circle')).toHaveLength(2);
    rerender(<InlineDocUploadingThumb filename="a.pdf" />);
    expect(screen.getByRole('progressbar')).toBeInTheDocument();
    expect(screen.queryByTestId('PictureAsPdfIcon')).toBeNull();
  });
});

describe('InlineDocAttachmentChip', () => {
  it('показывает имя, размер и кнопку удаления', () => {
    const onRemove = jest.fn();
    render(<InlineDocAttachmentChip name="отчёт.docx" size={2048} onRemove={onRemove} />);
    expect(screen.getByText('отчёт.docx')).toHaveAttribute('title', 'отчёт.docx');
    expect(screen.getByText('2.0 КБ')).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText('Удалить вложение отчёт.docx'));
    expect(onRemove).toHaveBeenCalledTimes(1);
  });

  it('sizeLabel приоритетнее size; нулевой размер скрыт; без onRemove кнопки нет', () => {
    const { rerender } = render(<InlineDocAttachmentChip name="a.txt" size={10} sizeLabel="10 штук" />);
    expect(screen.getByText('10 штук')).toBeInTheDocument();
    rerender(<InlineDocAttachmentChip name="a.txt" size={0} />);
    expect(screen.queryByText(/Б$/)).toBeNull();
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('removeDisabled блокирует кнопку; uploading убирает класс file-attachment', () => {
    const { container, rerender } = render(<InlineDocAttachmentChip name="a.pdf" onRemove={jest.fn()} removeDisabled />);
    expect(screen.getByRole('button')).toBeDisabled();
    expect(container.firstChild).toHaveClass('file-attachment');
    rerender(<InlineDocAttachmentChip name="a.pdf" uploading isMessage isDarkMode />);
    expect(container.firstChild).not.toHaveClass('file-attachment');
  });

  it('leading заменяет стандартную миниатюру', () => {
    render(<InlineDocAttachmentChip name="a.pdf" leading={<span data-testid="lead" />} />);
    expect(screen.getByTestId('lead')).toBeInTheDocument();
    expect(screen.queryByTestId('PictureAsPdfIcon')).toBeNull();
  });
});

describe('AuthenticatedInlineImage', () => {
  let fetchMock: jest.Mock;
  beforeEach(() => {
    mockToken = 'tok';
    fetchMock = jest.fn();
    (global as any).fetch = fetchMock;
    (URL as any).createObjectURL = jest.fn(() => 'blob:img');
    (URL as any).revokeObjectURL = jest.fn();
  });
  afterEach(() => jest.restoreAllMocks());

  it('data:/blob: ссылки показываются сразу без запросов', () => {
    const onExpand = jest.fn();
    render(<AuthenticatedInlineImage src="data:image/png;base64,AA" alt="pic" onExpand={onExpand} />);
    const img = screen.getByAltText('pic');
    expect(img).toHaveAttribute('src', 'data:image/png;base64,AA');
    fireEvent.click(img);
    expect(onExpand).toHaveBeenCalledWith('data:image/png;base64,AA');
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('клик без onExpand безопасен', () => {
    render(<AuthenticatedInlineImage src="blob:x" alt="pic" />);
    expect(() => fireEvent.click(screen.getByAltText('pic'))).not.toThrow();
  });

  it('удалённая ссылка грузится с Bearer-токеном и показывается как blob; при размонтировании blob освобождается', async () => {
    fetchMock.mockResolvedValue({ ok: true, blob: async () => new Blob(['x']) });
    const { unmount } = render(<AuthenticatedInlineImage src="http://minio/a.png" alt="pic" />);
    expect(screen.getByRole('progressbar')).toBeInTheDocument();
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(fetchMock).toHaveBeenCalledWith('http://minio/a.png', { headers: { Authorization: 'Bearer tok' } });
    expect(screen.getByAltText('pic')).toHaveAttribute('src', 'blob:img');
    unmount();
    expect((URL as any).revokeObjectURL).toHaveBeenCalledWith('blob:img');
  });

  it('без токена запроса нет, виден индикатор', () => {
    mockToken = null;
    render(<AuthenticatedInlineImage src="http://minio/a.png" alt="pic" sx={{ width: 10 }} />);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.getByRole('progressbar')).toBeInTheDocument();
  });

  it('HTTP-ошибка и сетевой сбой оставляют индикатор', async () => {
    fetchMock.mockResolvedValueOnce({ ok: false });
    const first = render(<AuthenticatedInlineImage src="http://minio/a.png" alt="pic" />);
    await act(async () => {
      await Promise.resolve();
    });
    expect(first.container.querySelector('img')).toBeNull();
    first.unmount();
    fetchMock.mockRejectedValueOnce(new Error('net'));
    const second = render(<AuthenticatedInlineImage src="http://minio/b.png" alt="pic" />);
    await act(async () => {
      await Promise.resolve();
    });
    expect(second.container.querySelector('img')).toBeNull();
  });

  it('размонтирование до ответа не создаёт blob', async () => {
    let resolve!: (v: unknown) => void;
    fetchMock.mockReturnValue(new Promise((r) => (resolve = r)));
    const { unmount } = render(<AuthenticatedInlineImage src="http://minio/a.png" alt="pic" />);
    unmount();
    await act(async () => resolve({ ok: true, blob: async () => new Blob(['x']) }));
    expect((URL as any).createObjectURL).not.toHaveBeenCalled();
  });

  it('смена src на data: переключает отображение', () => {
    const { rerender } = render(<AuthenticatedInlineImage src="http://minio/a.png" alt="pic" />);
    rerender(<AuthenticatedInlineImage src="data:image/png;base64,BB" alt="pic" />);
    expect(screen.getByAltText('pic')).toHaveAttribute('src', 'data:image/png;base64,BB');
  });

  it('ответ приходит после того, как blob уже скачан, но компонент размонтирован (cancelled после blob())', async () => {
    let resolveBlob!: (b: Blob) => void;
    fetchMock.mockResolvedValue({ ok: true, blob: () => new Promise<Blob>((r) => (resolveBlob = r)) });
    const { unmount } = render(<AuthenticatedInlineImage src="http://minio/a.png" alt="pic" />);
    await act(async () => {
      await Promise.resolve();
    });
    unmount();
    await act(async () => resolveBlob(new Blob(['x'])));
    expect((URL as any).createObjectURL).not.toHaveBeenCalled();
  });
});

describe('InlineAttachmentsList', () => {
  const files = [
    { name: 'pic.png', contentType: 'image' as const, imageSrc: 'data:image/png;base64,AA' },
    { name: 'remote.png', contentType: 'image' as const, imageSrc: 'http://minio/r.png' },
    { name: 'noimg.png', contentType: 'image' as const },
    { name: 'doc.pdf', contentType: 'text' as const, size: 2048 },
    { name: 'doc2.txt', contentType: 'text' as const, size: 0 },
  ];

  beforeEach(() => {
    mockToken = null; // удалённые превью остаются индикаторами
  });

  it('пустой список ничего не рисует', () => {
    const { container } = render(<InlineAttachmentsList files={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('разделяет изображения и документы; картинка без src не показывается как изображение', () => {
    render(<InlineAttachmentsList files={files} />);
    expect(screen.getByAltText('pic.png')).toBeInTheDocument();
    expect(screen.getByText('doc.pdf')).toBeInTheDocument();
    expect(screen.getByText('2.0 КБ')).toBeInTheDocument();
    // 'noimg.png' (image без src) попадает в блок документов? нет: contentType image => не документ и не картинка
    expect(screen.queryByText('noimg.png')).toBeNull();
    expect(screen.getByRole('progressbar')).toBeInTheDocument(); // удалённое превью
  });

  it('вариант input: кнопки удаления у картинок и документов вызывают onRemove с исходным индексом', () => {
    const onRemove = jest.fn();
    render(<InlineAttachmentsList files={files} onRemove={onRemove} />);
    const imgBox = screen.getByAltText('pic.png').parentElement as HTMLElement;
    fireEvent.click(imgBox.querySelector('button')!);
    expect(onRemove).toHaveBeenLastCalledWith(0);
    fireEvent.click(screen.getByLabelText('Удалить вложение doc.pdf'));
    expect(onRemove).toHaveBeenLastCalledWith(3);
  });

  it('вариант message: без кнопок удаления у картинок, клик по картинке вызывает onImageExpand', () => {
    const onImageExpand = jest.fn();
    render(<InlineAttachmentsList files={files} variant="message" onImageExpand={onImageExpand} isDarkMode sx={{ mt: 1 }} />);
    fireEvent.click(screen.getByAltText('pic.png'));
    expect(onImageExpand).toHaveBeenCalledWith('data:image/png;base64,AA', 'pic.png');
    expect(screen.getByAltText('pic.png').parentElement?.querySelector('button')).toBeNull();
  });

  it('удалённое изображение разворачивается через AuthenticatedInlineImage', async () => {
    mockToken = 'tok';
    const fetchMock = jest.fn().mockResolvedValue({ ok: true, blob: async () => new Blob(['x']) });
    (global as any).fetch = fetchMock;
    (URL as any).createObjectURL = jest.fn(() => 'blob:r');
    const onImageExpand = jest.fn();
    render(
      <InlineAttachmentsList
        files={[{ name: 'remote.png', contentType: 'image', imageSrc: 'http://minio/r.png' }]}
        variant="message"
        onImageExpand={onImageExpand}
      />,
    );
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    fireEvent.click(screen.getByAltText('remote.png'));
    expect(onImageExpand).toHaveBeenCalledWith('blob:r', 'remote.png');
  });

  it('без onImageExpand курсор не zoom-in и подсказки нет', () => {
    render(<InlineAttachmentsList files={[files[0]]} />);
    expect(getComputedStyle(screen.getByAltText('pic.png')).cursor).toBe('default');
  });
});
