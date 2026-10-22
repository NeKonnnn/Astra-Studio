/**
 * @jest-environment jsdom
 */
import { buildArtifactViewerDocument, openArtifactViewer } from '@/utils/artifactViewer';

const mockInitialize = jest.fn();
const mockRender = jest.fn();

jest.mock(
  'mermaid',
  () => ({
    __esModule: true,
    default: {
      initialize: (...args: unknown[]) => mockInitialize(...args),
      render: (...args: unknown[]) => mockRender(...args),
    },
  }),
  { virtual: true },
);

describe('artifactViewer mermaid security level', () => {
  beforeEach(() => {
    mockInitialize.mockReset();
    mockRender.mockReset();
    mockRender.mockResolvedValue({ svg: '<svg id="rendered"></svg>' });
  });

  it('initializes mermaid with a strict (non-loose) securityLevel', async () => {
    const html = await buildArtifactViewerDocument({
      type: 'application/vnd.mermaid',
      content: 'graph TD; A-->B;',
      title: 'Diagram',
    });

    expect(mockInitialize).toHaveBeenCalledTimes(1);
    const config = mockInitialize.mock.calls[0][0];
    expect(config.securityLevel).toBe('strict');
    expect(config.securityLevel).not.toBe('loose');
    expect(config.startOnLoad).toBe(false);
    expect(html).toContain('<svg id="rendered"></svg>');
  });
});

describe('artifactViewer SVG sanitization (BUG-FE-04)', () => {
  it('strips scripts, on* handlers and javascript: links from SVG artifacts', async () => {
    const html = await buildArtifactViewerDocument({
      type: 'image/svg+xml',
      content:
        '<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"><script>alert(2)</script>' +
        '<a href="javascript:alert(3)"><circle r="5" onclick="x()"/></a><rect width="1" height="1"/></svg>',
      title: 'Svg',
    });

    expect(html).not.toMatch(/<script/i);
    expect(html).not.toMatch(/\bon(load|click)\s*=/i);
    expect(html).not.toMatch(/javascript:/i);
    expect(html).toContain('<rect');
    expect(html).toContain('<circle');
  });

  it('sanitizes SVG returned by mermaid as well', async () => {
    mockRender.mockResolvedValue({ svg: '<svg><image href="x" onerror="alert(1)"/></svg>' });
    const html = await buildArtifactViewerDocument({
      type: 'application/vnd.mermaid',
      content: 'graph TD; A-->B;',
      title: 'Diagram',
    });
    expect(html).not.toMatch(/onerror/i);
  });

  it('keeps foreignObject labels (needed by mermaid)', async () => {
    mockRender.mockResolvedValue({
      svg: '<svg><foreignObject><div>Label</div></foreignObject></svg>',
    });
    const html = await buildArtifactViewerDocument({
      type: 'application/vnd.mermaid',
      content: 'graph TD; A-->B;',
    });
    expect(html).toContain('Label');
  });
});

describe('openArtifactViewer window.open features', () => {
  const originalOpen = window.open;

  beforeEach(() => {
    localStorage.clear();
    window.open = jest.fn(() => null) as unknown as typeof window.open;
  });

  afterEach(() => {
    window.open = originalOpen;
    jest.useRealTimers();
    localStorage.clear();
  });

  it('opens the viewer with noopener,noreferrer so the new tab has no window.opener', async () => {
    await openArtifactViewer({
      type: 'text/html',
      content: '<p>hello</p>',
      title: 'Doc',
    });

    expect(window.open).toHaveBeenCalledTimes(1);
    const [url, target, features] = (window.open as jest.Mock).mock.calls[0];
    expect(String(url)).toContain('/artifact-viewer.html?key=');
    expect(target).toBe('_blank');
    expect(features).toContain('noopener');
    expect(features).toContain('noreferrer');
  });

  it('does not throw when window.open returns null (always the case with noopener)', async () => {
    await expect(
      openArtifactViewer({ type: 'text/html', content: '<p>x</p>', title: 'Doc' }),
    ).resolves.toBeUndefined();
  });

  it('stores the artifact payload for the viewer page', async () => {
    await openArtifactViewer({ type: 'text/html', content: '<p>x</p>', title: 'Doc' });
    const keys = Object.keys(localStorage).filter((k) => k.startsWith('astrachat:artifact:'));
    expect(keys).toHaveLength(1);
    const payload = JSON.parse(localStorage.getItem(keys[0]) as string);
    expect(payload.title).toBe('Doc');
    expect(payload.content).toBe('<p>x</p>');
  });

  it('removes an orphaned artifact from localStorage if the tab never consumed it', async () => {
    jest.useFakeTimers();
    await openArtifactViewer({ type: 'text/html', content: '<p>x</p>', title: 'Doc' });
    expect(Object.keys(localStorage).some((k) => k.startsWith('astrachat:artifact:'))).toBe(true);

    jest.advanceTimersByTime(61_000);

    expect(Object.keys(localStorage).some((k) => k.startsWith('astrachat:artifact:'))).toBe(false);
  });
});
