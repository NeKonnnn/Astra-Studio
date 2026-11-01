import { dataUrlToFile, formatFileSize, getClipboardImageFile, prepareInlineImageFile } from '@/utils/inlineImage';

jest.mock('@/utils/chatAttachDebug', () => ({
  logChatAttach: jest.fn(),
  logChatAttachError: jest.fn(),
}));

describe('formatFileSize', () => {
  it.each([
    [0, '0 Б'],
    [1023, '1023 Б'],
    [1024, '1.0 КБ'],
    [1536, '1.5 КБ'],
    [1024 * 1024, '1.0 МБ'],
    [5.5 * 1024 * 1024, '5.5 МБ'],
  ])('%p -> %p', (bytes, expected) => {
    expect(formatFileSize(bytes)).toBe(expected);
  });
});

describe('dataUrlToFile', () => {
  it('decodes base64 data into a file with the right type and name', async () => {
    const file = dataUrlToFile('data:text/plain;base64,aGVsbG8=', 'hello.txt');

    expect(file.name).toBe('hello.txt');
    expect(file.type).toBe('text/plain');
    expect(file.size).toBe(5);
  });

  it('defaults the mime type to jpeg', () => {
    expect(dataUrlToFile(',aGk=', 'a.jpg').type).toBe('image/jpeg');
  });
});

describe('getClipboardImageFile', () => {
  const makeItem = (type: string, blob: File | null, kind = 'file') => ({
    kind,
    type,
    getAsFile: () => blob,
  });

  it('returns null for an empty clipboard', () => {
    expect(getClipboardImageFile({ items: [], files: [] } as unknown as DataTransfer)).toBeNull();
    expect(getClipboardImageFile({} as DataTransfer)).toBeNull();
  });

  it('takes the first image item and keeps its name', () => {
    const file = new File(['x'], 'shot.png', { type: 'image/png' });
    const data = {
      items: [makeItem('text/plain', null, 'string'), makeItem('image/png', file)],
      files: [],
    } as unknown as DataTransfer;

    expect(getClipboardImageFile(data)).toBe(file);
  });

  it('names nameless clipboard images by their extension', () => {
    const blob = new File(['x'], '', { type: 'image/jpeg' });
    const data = { items: [makeItem('image/jpeg', blob)], files: [] } as unknown as DataTransfer;

    const result = getClipboardImageFile(data)!;

    expect(result.name).toMatch(/^screenshot_\d+\.jpeg$/);
    expect(result.type).toBe('image/jpeg');
  });

  it('skips non-image files and items without a blob', () => {
    const data = {
      items: [makeItem('image/png', null), makeItem('application/pdf', new File(['x'], 'a.pdf'))],
      files: [new File(['x'], 'a.pdf', { type: 'application/pdf' })],
    } as unknown as DataTransfer;

    expect(getClipboardImageFile(data)).toBeNull();
  });

  it('falls back to the files list', () => {
    const file = new File(['x'], 'pic.gif', { type: 'image/gif' });
    const data = { items: [], files: [file] } as unknown as DataTransfer;

    expect(getClipboardImageFile(data)).toBe(file);
  });
});

describe('prepareInlineImageFile', () => {
  const original = {
    createObjectURL: URL.createObjectURL,
    revokeObjectURL: URL.revokeObjectURL,
    Image: (global as unknown as { Image: unknown }).Image,
    getContext: HTMLCanvasElement.prototype.getContext,
    toDataURL: HTMLCanvasElement.prototype.toDataURL,
  };
  let toDataURL: jest.Mock;
  let drawImage: jest.Mock;
  let imageSize = { w: 100, h: 50 };
  let imageFails = false;

  beforeEach(() => {
    imageSize = { w: 100, h: 50 };
    imageFails = false;
    URL.createObjectURL = jest.fn(() => 'blob:test');
    URL.revokeObjectURL = jest.fn();
    drawImage = jest.fn();
    toDataURL = jest.fn((type: string) => `data:${type};base64,${'A'.repeat(100)}`);
    HTMLCanvasElement.prototype.getContext = jest.fn(() => ({ drawImage })) as never;
    HTMLCanvasElement.prototype.toDataURL = toDataURL as never;
    (global as unknown as { Image: unknown }).Image = class {
      naturalWidth = imageSize.w;
      naturalHeight = imageSize.h;
      onload: (() => void) | null = null;
      onerror: (() => void) | null = null;
      set src(_value: string) {
        setTimeout(() => (imageFails ? this.onerror?.() : this.onload?.()));
      }
    };
  });

  afterEach(() => {
    URL.createObjectURL = original.createObjectURL;
    URL.revokeObjectURL = original.revokeObjectURL;
    (global as unknown as { Image: unknown }).Image = original.Image;
    HTMLCanvasElement.prototype.getContext = original.getContext;
    HTMLCanvasElement.prototype.toDataURL = original.toDataURL;
  });

  it('keeps small jpeg images as jpeg and renames the extension', async () => {
    const file = new File(['x'.repeat(2000)], 'photo.heic', { type: 'image/jpeg' });

    const result = await prepareInlineImageFile(file);

    expect(result.filename).toBe('photo.jpg');
    expect(result.dataUrl.startsWith('data:image/jpeg')).toBe(true);
    expect(toDataURL).toHaveBeenCalledWith('image/jpeg', 0.88);
    expect(result.wasCompressed).toBe(true);
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:test');
  });

  it('keeps png and gif as png', async () => {
    const result = await prepareInlineImageFile(new File(['x'], 'shot.png', { type: 'image/png' }));

    expect(result.filename).toBe('shot.png');
    expect(toDataURL).toHaveBeenCalledWith('image/png');
  });

  it('downscales images larger than 2048 px on the longest side', async () => {
    imageSize = { w: 4096, h: 1024 };
    let canvasSize = { w: 0, h: 0 };
    drawImage.mockImplementation((_img, _x, _y, w: number, h: number) => {
      canvasSize = { w, h };
    });

    const result = await prepareInlineImageFile(new File(['x'], 'big.jpg', { type: 'image/jpeg' }));

    expect(canvasSize).toEqual({ w: 2048, h: 512 });
    expect(result.wasCompressed).toBe(true);
  });

  it('converts an oversized png to jpeg', async () => {
    toDataURL.mockImplementation((type: string) =>
      type === 'image/png' ? `data:image/png;base64,${'A'.repeat(6_000_000)}` : `data:image/jpeg;base64,${'A'.repeat(100)}`,
    );

    const result = await prepareInlineImageFile(new File(['x'], 'huge.png', { type: 'image/png' }));

    expect(result.dataUrl.startsWith('data:image/jpeg')).toBe(true);
    expect(result.filename).toBe('huge.jpg');
  });

  it('lowers the jpeg quality step by step down to the floor', async () => {
    const qualities: number[] = [];
    toDataURL.mockImplementation((type: string, quality?: number) => {
      if (quality !== undefined) qualities.push(quality);
      return `data:${type};base64,${'A'.repeat(6_000_000)}`;
    });

    await prepareInlineImageFile(new File(['x'], 'p.jpg', { type: 'image/jpeg' }));

    expect(qualities[0]).toBe(0.88);
    // Порог проверяется до шага вниз, поэтому последний вызов идёт на 0.48 < 0.55 (см. KNOWN_BUGS).
    expect(qualities[qualities.length - 1]).toBe(0.48);
    expect(qualities.slice(1)).toEqual([...qualities.slice(1)].sort((a, b) => b - a));
  });

  it('rejects when the image cannot be decoded and still releases the object URL', async () => {
    imageFails = true;

    await expect(prepareInlineImageFile(new File(['x'], 'bad.png', { type: 'image/png' }))).rejects.toThrow(
      'invalid-image',
    );
    expect(URL.revokeObjectURL).toHaveBeenCalled();
  });

  it('rejects when canvas is unavailable', async () => {
    HTMLCanvasElement.prototype.getContext = jest.fn(() => null) as never;

    await expect(prepareInlineImageFile(new File(['x'], 'a.png', { type: 'image/png' }))).rejects.toThrow(
      'canvas-unavailable',
    );
  });
});
