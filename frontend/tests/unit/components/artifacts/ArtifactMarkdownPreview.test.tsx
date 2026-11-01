import React from 'react';
import { render, screen } from '@testing-library/react';
import '@testing-library/jest-dom';
import ArtifactMarkdownPreview from '@/components/artifacts/ArtifactMarkdownPreview';

// react-markdown / remark / rehype — ESM-пакеты, Jest 27 (CRA) их не загружает: мокаем и
// проверяем, какие плагины и какой исходный текст получает рендерер.
const mdProps: any[] = [];
jest.mock('react-markdown', () => ({
  __esModule: true,
  default: (p: any) => {
    mdProps.push(p);
    return <div data-testid="md">{p.children}</div>;
  },
}));
jest.mock('remark-gfm', () => ({ __esModule: true, default: 'remark-gfm' }));
jest.mock('remark-math', () => ({ __esModule: true, default: 'remark-math' }));
jest.mock('rehype-katex', () => ({ __esModule: true, default: 'rehype-katex' }));

describe('ArtifactMarkdownPreview', () => {
  beforeEach(() => {
    mdProps.length = 0;
  });

  it('передаёт markdown в рендерер', () => {
    render(<ArtifactMarkdownPreview content={'# Заголовок\n\nтекст'} />);
    expect(screen.getByTestId('md')).toHaveTextContent('# Заголовок');
    expect(screen.getByTestId('md')).toHaveTextContent('текст');
  });

  it('пустой/отсутствующий контент -> пустая строка', () => {
    render(<ArtifactMarkdownPreview content={undefined as unknown as string} />);
    expect(mdProps[0].children).toBe('');
  });

  it('подключает GFM, математику ($...$ включён) и KaTeX', () => {
    render(<ArtifactMarkdownPreview content="x" />);
    const { remarkPlugins, rehypePlugins } = mdProps[0];
    expect(remarkPlugins[0]).toBe('remark-gfm');
    expect(remarkPlugins[1]).toEqual(['remark-math', { singleDollarTextMath: true }]);
    expect(rehypePlugins).toEqual(['rehype-katex']);
  });

  it('сырой HTML не включается: rehype-raw в плагинах отсутствует (XSS через <script>/on* невозможен)', () => {
    render(<ArtifactMarkdownPreview content={'<img src=x onerror="alert(1)"><script>alert(2)</script>'} />);
    const names = (mdProps[0].rehypePlugins as unknown[]).map(String);
    expect(names.join(',')).not.toMatch(/raw/i);
    // Рендерер получает markdown-текст, а не DOM: в разметке нет исполняемых элементов.
    expect(document.querySelector('script')).toBeNull();
  });

  it('денежные суммы со знаком $ экранируются, чтобы не стать формулой; код не трогается', () => {
    render(<ArtifactMarkdownPreview content={'Цена $100 и 5$\n`$x$`'} />);
    const prepared = mdProps[0].children as string;
    expect(prepared).toContain('\\$100');
    expect(prepared).toContain('`$x$`');
  });

  it('контент без $ передаётся как есть', () => {
    render(<ArtifactMarkdownPreview content={'просто **текст**'} />);
    expect(mdProps[0].children).toBe('просто **текст**');
  });
});
