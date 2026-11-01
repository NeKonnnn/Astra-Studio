import React from 'react';
import { render, screen } from '@testing-library/react';
import '@testing-library/jest-dom';
import ArtifactPreview from '@/components/artifacts/ArtifactPreview';
import type { ChatArtifact } from '@/types/artifacts';

jest.mock('@/components/artifacts/ArtifactHtmlPreview', () => ({
  __esModule: true,
  default: (p: { content: string; isStreaming: boolean }) => (
    <div data-testid="html" data-streaming={String(p.isStreaming)}>{p.content}</div>
  ),
}));
jest.mock('@/components/artifacts/ArtifactMarkdownPreview', () => ({
  __esModule: true,
  default: (p: { content: string }) => <div data-testid="md">{p.content}</div>,
}));
jest.mock('@/components/artifacts/ArtifactMermaidPreview', () => ({
  __esModule: true,
  default: (p: { content: string; isStreaming: boolean }) => (
    <div data-testid="mermaid" data-streaming={String(p.isStreaming)}>{p.content}</div>
  ),
}));
jest.mock('@/components/artifacts/ArtifactSvgPreview', () => ({
  __esModule: true,
  default: (p: { content: string; isStreaming: boolean }) => (
    <div data-testid="svg" data-streaming={String(p.isStreaming)}>{p.content}</div>
  ),
}));
jest.mock('@/components/artifacts/ArtifactReactPreview', () => ({
  __esModule: true,
  default: (p: { content: string; isStreaming: boolean }) => (
    <div data-testid="react" data-streaming={String(p.isStreaming)}>{p.content}</div>
  ),
}));

function art(type: string, content = 'тело', closed = true): ChatArtifact {
  return { id: 'a1', identifier: 'x', type, title: 't', content, closed };
}

describe('ArtifactPreview', () => {
  it.each([
    ['text/html', 'html'],
    ['application/vnd.code-html', 'html'],
    ['image/svg+xml', 'svg'],
    ['text/markdown', 'md'],
    ['text/md', 'md'],
    ['application/vnd.mermaid', 'mermaid'],
    ['application/vnd.react', 'react'],
    ['application/vnd.ant.react', 'react'],
  ])('тип %s -> превью %s с исходным содержимым', (type, testId) => {
    render(<ArtifactPreview artifact={art(type, 'контент')} />);
    expect(screen.getByTestId(testId)).toHaveTextContent('контент');
  });

  it('тип определяется без учёта регистра', () => {
    render(<ArtifactPreview artifact={art('TEXT/HTML')} />);
    expect(screen.getByTestId('html')).toBeInTheDocument();
  });

  it('isStreaming приводится к булеву и передаётся в превью', () => {
    const { rerender } = render(<ArtifactPreview artifact={art('text/html')} isStreaming />);
    expect(screen.getByTestId('html')).toHaveAttribute('data-streaming', 'true');
    rerender(<ArtifactPreview artifact={art('text/html')} />);
    expect(screen.getByTestId('html')).toHaveAttribute('data-streaming', 'false');
  });

  it('незакрытый артефакт без текста на стриме — пустая заглушка, превью не создаётся', () => {
    const { container } = render(
      <ArtifactPreview artifact={art('text/html', '   ', false)} isStreaming />,
    );
    expect(screen.queryByTestId('html')).toBeNull();
    expect(container.firstChild).toBeInTheDocument();
    expect(container).toHaveTextContent('');
  });

  it('незакрытый артефакт с текстом на стриме уже показывает превью', () => {
    render(<ArtifactPreview artifact={art('text/html', 'часть', false)} isStreaming />);
    expect(screen.getByTestId('html')).toHaveTextContent('часть');
  });

  it('закрытый пустой артефакт не считается «пустым стримом»', () => {
    render(<ArtifactPreview artifact={art('text/html', '', true)} isStreaming />);
    expect(screen.getByTestId('html')).toBeInTheDocument();
  });

  it('неизвестный тип: сообщение и исходник как текст (разметка не интерпретируется)', () => {
    const { container } = render(
      <ArtifactPreview artifact={art('application/x-foo', '<img src=x onerror="alert(1)">')} />,
    );
    expect(screen.getByText(/Предпросмотр для типа «application\/x-foo» недоступен/)).toBeInTheDocument();
    const pre = container.querySelector('pre')!;
    expect(pre.textContent).toBe('<img src=x onerror="alert(1)">');
    expect(container.querySelector('img')).toBeNull();
  });
});
