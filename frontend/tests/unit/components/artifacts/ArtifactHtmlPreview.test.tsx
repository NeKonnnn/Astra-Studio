import React from 'react';
import { render, screen } from '@testing-library/react';
import '@testing-library/jest-dom';
import ArtifactHtmlPreview from '@/components/artifacts/ArtifactHtmlPreview';

jest.mock('@/components/InlinePresentationViewer', () => ({
  __esModule: true,
  default: (p: { html: string; isStreaming?: boolean; embedded?: boolean }) => (
    <div
      data-testid="deck-viewer"
      data-streaming={String(Boolean(p.isStreaming))}
      data-embedded={String(Boolean(p.embedded))}
    >
      {p.html}
    </div>
  ),
}));

const DECK =
  '<div class="slide"><h1>Заголовок презентации</h1></div><div class="slide"><p>Второй слайд</p></div>';

function frame(): HTMLIFrameElement {
  return screen.getByTitle('HTML artifact preview') as HTMLIFrameElement;
}

describe('ArtifactHtmlPreview', () => {
  it('презентация (.slide) открывается во встроенном InlinePresentationViewer, без iframe', () => {
    render(<ArtifactHtmlPreview content={DECK} />);
    const viewer = screen.getByTestId('deck-viewer');
    expect(viewer).toHaveAttribute('data-embedded', 'true');
    expect(viewer).toHaveAttribute('data-streaming', 'false');
    expect(viewer).toHaveTextContent('Заголовок презентации');
    expect(screen.queryByTitle('HTML artifact preview')).toBeNull();
  });

  it('презентация во время стрима пробрасывает isStreaming во viewer', () => {
    render(<ArtifactHtmlPreview content={DECK} isStreaming />);
    expect(screen.getByTestId('deck-viewer')).toHaveAttribute('data-streaming', 'true');
  });

  it('во время стрима обычного HTML iframe не монтируется (только заглушка)', () => {
    const { container } = render(<ArtifactHtmlPreview content="<p>привет</p>" isStreaming />);
    expect(screen.queryByTitle('HTML artifact preview')).toBeNull();
    expect(container.querySelector('iframe')).toBeNull();
    expect(container.firstChild).toBeInTheDocument();
  });

  it('HTML-фрагмент оборачивается в полноценный документ с кириллической локалью', () => {
    render(<ArtifactHtmlPreview content="<p>привет</p>" />);
    const doc = frame().getAttribute('srcdoc')!;
    expect(doc).toContain('<!DOCTYPE html>');
    expect(doc).toContain('<html lang="ru">');
    expect(doc).toContain('<body>\n<p>привет</p>\n</body>');
  });

  it('полный документ (doctype или <html>) не оборачивается повторно', () => {
    const full = '<!DOCTYPE html><html><body><b>x</b></body></html>';
    const { rerender } = render(<ArtifactHtmlPreview content={full} />);
    expect(frame().getAttribute('srcdoc')).toBe(full);
    const noDoctype = '<html lang="en"><body>y</body></html>';
    rerender(<ArtifactHtmlPreview content={noDoctype} />);
    expect(frame().getAttribute('srcdoc')).toBe(noDoctype);
  });

  it('Chart.js с CDN подменяется на локальный vendor-файл (офлайн-режим)', () => {
    const html =
      '<html><head><script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script></head>' +
      '<body><script>new Chart(document.body, {})</script></body></html>';
    render(<ArtifactHtmlPreview content={html} />);
    const doc = frame().getAttribute('srcdoc')!;
    expect(doc).not.toContain('cdn.jsdelivr.net');
    expect(doc).toContain('/vendor/chart.umd.min.js');
  });

  it('пустое содержимое даёт iframe с пустым документом-обёрткой', () => {
    render(<ArtifactHtmlPreview content={undefined as unknown as string} />);
    expect(frame().getAttribute('srcdoc')).toContain('<body>\n\n</body>');
  });

  it('после окончания стрима iframe появляется с финальным содержимым', () => {
    const { rerender } = render(<ArtifactHtmlPreview content="<p>a</p>" isStreaming />);
    expect(screen.queryByTitle('HTML artifact preview')).toBeNull();
    rerender(<ArtifactHtmlPreview content="<p>итог</p>" isStreaming={false} />);
    expect(frame().getAttribute('srcdoc')).toContain('<p>итог</p>');
  });

  it('inline-скрипты модели остаются в srcdoc как есть (исполнение — на стороне sandbox iframe)', () => {
    render(<ArtifactHtmlPreview content={'<p>x</p><script>window.__pwn = 1</script>'} />);
    expect(frame().getAttribute('srcdoc')).toContain('<script>window.__pwn = 1</script>');
  });

  it('sandbox iframe содержит allow-scripts без allow-same-origin: код модели не получает origin приложения', () => {
    render(<ArtifactHtmlPreview content={'<script>parent.localStorage.getItem("token")</script>'} />);
    const tokens = (frame().getAttribute('sandbox') || '').split(/\s+/);
    expect(tokens).toContain('allow-scripts');
    expect(tokens).not.toContain('allow-same-origin');
  });
});
