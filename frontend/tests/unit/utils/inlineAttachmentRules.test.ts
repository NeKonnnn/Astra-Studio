import {
  INLINE_ATTACHMENT_MAX_BYTES,
  INLINE_ATTACH_ACCEPT,
  INLINE_ATTACH_OVERSIZED_MESSAGE,
  buildOversizedInlineAttachMessage,
  buildUnsupportedInlineAttachMessage,
  getInlineAttachmentExtension,
  isInlineAttachFileAllowed,
  isInlineAttachFileTooLarge,
  isInlineAttachSizeErrorMessage,
} from '@/utils/inlineAttachmentRules';

function file(name: string, type = '', size = 10): File {
  const f = new File(['x'], name, { type });
  Object.defineProperty(f, 'size', { value: size });
  return f;
}

describe('getInlineAttachmentExtension', () => {
  it.each([
    ['report.PDF', '.pdf'],
    ['archive.tar.gz', '.gz'],
    ['noext', ''],
    ['.gitignore', ''],
    ['trailingdot.', ''],
    ['', ''],
  ])('%p -> %p', (name, expected) => {
    expect(getInlineAttachmentExtension(name)).toBe(expected);
  });
});

describe('isInlineAttachFileAllowed', () => {
  it.each([
    'a.pdf', 'a.docx', 'a.docm', 'a.doc', 'a.xlsx', 'a.xls', 'a.xlsm', 'a.pptx', 'a.ppt',
    'a.txt', 'a.md', 'a.log', 'a.rtf', 'a.jpg', 'a.jpeg', 'a.png', 'a.webp', 'a.gif', 'a.bmp',
  ])('allows %s by extension', (name) => {
    expect(isInlineAttachFileAllowed(file(name))).toBe(true);
  });

  it('is case-insensitive for extensions', () => {
    expect(isInlineAttachFileAllowed(file('SCAN.JPG'))).toBe(true);
    expect(isInlineAttachFileAllowed(file('Doc.DocX'))).toBe(true);
  });

  it('allows a file by MIME type even without an extension', () => {
    expect(isInlineAttachFileAllowed(file('blob', 'application/pdf'))).toBe(true);
    expect(isInlineAttachFileAllowed(file('blob', 'image/png'))).toBe(true);
  });

  it.each(['a.exe', 'a.zip', 'a.js', 'a.html', 'a.svg', 'a', 'a.pdf.exe'])(
    'rejects %s',
    (name) => {
      expect(isInlineAttachFileAllowed(file(name, 'application/octet-stream'))).toBe(false);
    },
  );

  it('only checks the final extension', () => {
    expect(isInlineAttachFileAllowed(file('evil.exe.txt'))).toBe(true);
  });

  it('trusts a declared MIME type over the extension (client-side check only)', () => {
    // Клиентская проверка не защита: сервер обязан валидировать содержимое сам.
    expect(isInlineAttachFileAllowed(file('payload.exe', 'application/pdf'))).toBe(true);
  });
});

describe('isInlineAttachFileTooLarge', () => {
  it('allows files up to the limit inclusive', () => {
    expect(isInlineAttachFileTooLarge(file('a.pdf', '', INLINE_ATTACHMENT_MAX_BYTES))).toBe(false);
  });

  it('rejects files over the limit', () => {
    expect(isInlineAttachFileTooLarge(file('a.pdf', '', INLINE_ATTACHMENT_MAX_BYTES + 1))).toBe(true);
  });

  it('uses a 20 MB limit', () => {
    expect(INLINE_ATTACHMENT_MAX_BYTES).toBe(20 * 1024 * 1024);
  });
});

describe('isInlineAttachSizeErrorMessage', () => {
  it.each(['File too large', 'HTTP 413', 'Размер файла превышает лимит', 'size exceeded', 'limit reached'])(
    'detects %p',
    (detail) => {
      expect(isInlineAttachSizeErrorMessage(detail)).toBe(true);
    },
  );

  it.each(['Unsupported media type', 'Network error', ''])('ignores %p', (detail) => {
    expect(isInlineAttachSizeErrorMessage(detail)).toBe(false);
  });
});

describe('messages', () => {
  it('names the extension of an unsupported file', () => {
    const message = buildUnsupportedInlineAttachMessage('virus.EXE');

    expect(message).toContain('.exe');
    expect(message).toContain('Допускаются следующие файлы');
  });

  it('handles files without extension', () => {
    expect(buildUnsupportedInlineAttachMessage('README')).toContain('без расширения');
  });

  it('returns the fixed oversize message regardless of arguments', () => {
    expect(buildOversizedInlineAttachMessage('a.pdf', 99)).toBe(INLINE_ATTACH_OVERSIZED_MESSAGE);
    expect(buildOversizedInlineAttachMessage()).toBe(INLINE_ATTACH_OVERSIZED_MESSAGE);
  });

  it('keeps the accept list in sync with the allowed extensions', () => {
    for (const ext of ['pdf', 'docx', 'xlsx', 'pptx', 'txt', 'png', 'jpeg']) {
      expect(INLINE_ATTACH_ACCEPT.split(',')).toContain(`.${ext}`);
      expect(isInlineAttachFileAllowed(file(`f.${ext}`))).toBe(true);
    }
  });
});
