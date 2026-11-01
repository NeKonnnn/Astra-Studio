import { exportArtifactPreviewAsJpg, exportArtifactPreviewAsPng } from '@/utils/artifactDownload';

const mockToCanvas = jest.fn();

jest.mock('html-to-image', () => ({
  toCanvas: (...args: unknown[]) => mockToCanvas(...args),
}));

function fakeCanvas(blob: Blob | null) {
  return {
    toBlob: jest.fn((cb: (b: Blob | null) => void) => cb(blob)),
  };
}

describe('artifact preview export', () => {
  let click: jest.SpyInstance;
  let created: HTMLAnchorElement[];
  const original = { create: URL.createObjectURL, revoke: URL.revokeObjectURL };

  beforeEach(() => {
    mockToCanvas.mockReset();
    created = [];
    URL.createObjectURL = jest.fn(() => 'blob:x');
    URL.revokeObjectURL = jest.fn();
    click = jest.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      created.push(this);
    });
  });

  afterEach(() => {
    click.mockRestore();
    URL.createObjectURL = original.create;
    URL.revokeObjectURL = original.revoke;
  });

  it('fails clearly without a preview root', async () => {
    await expect(exportArtifactPreviewAsPng({ previewRoot: null })).rejects.toThrow('ещё не готов');
  });

  it('downloads a png with a sanitized file name', async () => {
    mockToCanvas.mockResolvedValue(fakeCanvas(new Blob(['x'], { type: 'image/png' })));
    const root = document.createElement('div');

    await exportArtifactPreviewAsPng({ previewRoot: root, fileNameBase: 'Моя схема/v1?' });

    expect(created).toHaveLength(1);
    // `\w` — только ASCII: вся кириллица схлопывается в один "_" (см. KNOWN_BUGS).
    expect(created[0].download).toBe('_v1_.png');
    expect(created[0].rel).toBe('noopener');
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:x');
  });

  it('downloads a jpg with quality 0.92', async () => {
    const canvas = fakeCanvas(new Blob(['x']));
    mockToCanvas.mockResolvedValue(canvas);

    await exportArtifactPreviewAsJpg({ previewRoot: document.createElement('div'), fileNameBase: 'chart' });

    expect(canvas.toBlob).toHaveBeenCalledWith(expect.any(Function), 'image/jpeg', 0.92);
    expect(created[0].download).toBe('chart.jpg');
  });

  it('uses a default name', async () => {
    mockToCanvas.mockResolvedValue(fakeCanvas(new Blob(['x'])));

    await exportArtifactPreviewAsPng({ previewRoot: document.createElement('div') });

    expect(created[0].download).toBe('artifact.png');
  });

  it('exports the parent of the rendered svg', async () => {
    mockToCanvas.mockResolvedValue(fakeCanvas(new Blob(['x'])));
    const root = document.createElement('div');
    const host = document.createElement('section');
    host.appendChild(document.createElementNS('http://www.w3.org/2000/svg', 'svg'));
    root.appendChild(host);

    await exportArtifactPreviewAsPng({ previewRoot: root });

    expect(mockToCanvas.mock.calls[0][0]).toBe(host);
  });

  it('exports the whole root when it has no svg or iframe', async () => {
    mockToCanvas.mockResolvedValue(fakeCanvas(new Blob(['x'])));
    const root = document.createElement('div');

    await exportArtifactPreviewAsPng({ previewRoot: root });

    expect(mockToCanvas.mock.calls[0][0]).toBe(root);
    expect(mockToCanvas.mock.calls[0][1]).toMatchObject({ pixelRatio: 2, backgroundColor: '#ffffff', width: 1600, height: 900 });
  });

  it('reports a failed image conversion', async () => {
    mockToCanvas.mockResolvedValue(fakeCanvas(null));

    await expect(exportArtifactPreviewAsJpg({ previewRoot: document.createElement('div') })).rejects.toThrow(
      'Не удалось собрать JPG',
    );
    expect(click).not.toHaveBeenCalled();
  });
});
