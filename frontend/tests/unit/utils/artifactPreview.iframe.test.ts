import { exportArtifactPreviewAsPng } from '@/utils/artifactDownload';
import { captureHtmlFromPreviewRoot } from '@/utils/artifactViewer';

const mockToCanvas = jest.fn();

jest.mock('html-to-image', () => ({
  toCanvas: (...args: unknown[]) => mockToCanvas(...args),
}));

jest.mock('mermaid', () => ({ __esModule: true, default: { initialize: jest.fn(), render: jest.fn() } }));

function rootWithIframe(): { root: HTMLElement; iframe: HTMLIFrameElement } {
  const root = document.createElement('div');
  const iframe = document.createElement('iframe');
  root.appendChild(iframe);
  document.body.appendChild(root);
  return { root, iframe };
}

describe('экспорт preview из iframe', () => {
  beforeEach(() => {
    mockToCanvas.mockReset();
    (URL as any).createObjectURL = jest.fn(() => 'blob:x');
    (URL as any).revokeObjectURL = jest.fn();
    jest.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
  });

  afterEach(() => {
    document.body.innerHTML = '';
    jest.restoreAllMocks();
  });

  it('PNG: размеры берутся из документа iframe, цель - его body', async () => {
    const { root, iframe } = rootWithIframe();
    const body = document.createElement('body');
    const doc = {
      body,
      documentElement: { scrollWidth: 1234.4, scrollHeight: 0 },
    };
    Object.defineProperty(doc.body, 'scrollHeight', { value: 777.6 });
    Object.defineProperty(iframe, 'contentDocument', { value: doc });
    mockToCanvas.mockResolvedValue({ toBlob: (cb: (b: Blob) => void) => cb(new Blob(['x'])) });
    await exportArtifactPreviewAsPng({ previewRoot: root, fileNameBase: 'doc' });
    const [node, opts] = mockToCanvas.mock.calls[0];
    expect(node).toBe(body);
    expect(opts.width).toBe(1234);
    expect(opts.canvasWidth).toBe(1234);
    expect(opts.height).toBe(778);
  });

  it('PNG: без размеров у документа используются значения по умолчанию 1600x900', async () => {
    const { root, iframe } = rootWithIframe();
    const doc = { body: document.createElement('body'), documentElement: {} };
    Object.defineProperty(iframe, 'contentDocument', { value: doc });
    mockToCanvas.mockResolvedValue({ toBlob: (cb: (b: Blob) => void) => cb(new Blob(['x'])) });
    await exportArtifactPreviewAsPng({ previewRoot: root });
    const opts = mockToCanvas.mock.calls[0][1];
    expect(opts.width).toBe(1600);
    expect(opts.height).toBe(900);
  });
});

describe('captureHtmlFromPreviewRoot: iframe без srcdoc', () => {
  afterEach(() => {
    document.body.innerHTML = '';
  });

  it('собирает HTML из contentDocument', () => {
    const { root, iframe } = rootWithIframe();
    Object.defineProperty(iframe, 'srcdoc', { value: '   ', configurable: true });
    Object.defineProperty(iframe, 'contentDocument', {
      value: { documentElement: { outerHTML: '<html><body>X</body></html>' } },
    });
    expect(captureHtmlFromPreviewRoot(root)).toBe('<!DOCTYPE html>\n<html><body>X</body></html>');
  });

  it('если доступ к contentDocument запрещён (cross-origin), возвращает null', () => {
    const { root, iframe } = rootWithIframe();
    Object.defineProperty(iframe, 'contentDocument', {
      get() {
        throw new Error('SecurityError');
      },
    });
    expect(captureHtmlFromPreviewRoot(root)).toBeNull();
  });

  it('пустой iframe без documentElement даёт null', () => {
    const { root, iframe } = rootWithIframe();
    Object.defineProperty(iframe, 'contentDocument', { value: { documentElement: null } });
    expect(captureHtmlFromPreviewRoot(root)).toBeNull();
  });
});
