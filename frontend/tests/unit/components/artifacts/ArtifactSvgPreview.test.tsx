import React from 'react';
import { render, screen } from '@testing-library/react';
import '@testing-library/jest-dom';
import ArtifactSvgPreview from '@/components/artifacts/ArtifactSvgPreview';

function frame(): HTMLIFrameElement {
  return screen.getByTitle('SVG artifact preview') as HTMLIFrameElement;
}

describe('ArtifactSvgPreview', () => {
  it('оборачивает SVG в HTML-документ с центрированием и кладёт в srcdoc', () => {
    render(<ArtifactSvgPreview content={'  <svg viewBox="0 0 1 1"><circle r="1"/></svg>  '} />);
    const doc = frame().getAttribute('srcdoc')!;
    expect(doc).toContain('<!DOCTYPE html>');
    expect(doc).toContain('<body><svg viewBox="0 0 1 1"><circle r="1"/></svg></body>');
    expect(doc).toContain('justify-content:center');
  });

  it('SVG в iframe с пустым sandbox: скрипты внутри SVG не исполняются', () => {
    render(
      <ArtifactSvgPreview content={'<svg onload="alert(1)"><script>alert(2)</script></svg>'} />,
    );
    // Атрибут sandbox="" (без allow-scripts / allow-same-origin) запрещает любой JS в документе.
    expect(frame().getAttribute('sandbox')).toBe('');
  });

  it('распознаёт <svg> без учёта регистра', () => {
    render(<ArtifactSvgPreview content={'<SVG width="1"></SVG>'} />);
    expect(frame().getAttribute('srcdoc')).toContain('<body><SVG width="1"></SVG></body>');
  });

  it('текст без <svg> показывается как <pre> с экранированным "<"', () => {
    render(<ArtifactSvgPreview content={'<div onclick="x()">привет</div>'} />);
    const doc = frame().getAttribute('srcdoc')!;
    expect(doc).toContain('<pre>&lt;div onclick="x()">привет&lt;/div></pre>');
    expect(doc).not.toContain('<pre><div');
  });

  it('пустое/пробельное содержимое даёт пустой srcdoc', () => {
    const { rerender } = render(<ArtifactSvgPreview content="   " />);
    expect(frame().getAttribute('srcdoc')).toBe('');
    rerender(<ArtifactSvgPreview content={undefined as unknown as string} />);
    expect(frame().getAttribute('srcdoc')).toBe('');
  });

  it('во время стрима показывает актуальное содержимое, после стрима — зафиксированное', () => {
    const { rerender } = render(<ArtifactSvgPreview content="<svg>a</svg>" isStreaming />);
    expect(frame().getAttribute('srcdoc')).toContain('<svg>a</svg>');
    rerender(<ArtifactSvgPreview content="<svg>ab</svg>" isStreaming />);
    expect(frame().getAttribute('srcdoc')).toContain('<svg>ab</svg>');
    rerender(<ArtifactSvgPreview content="<svg>final</svg>" isStreaming={false} />);
    expect(frame().getAttribute('srcdoc')).toContain('<svg>final</svg>');
  });
});
