import katex from 'katex';
import { preprocessArtifactLatex, renderKatexHtml } from '@/utils/chatMath';

describe('chatMath: недостающие ветки', () => {
  afterEach(() => jest.restoreAllMocks());

  it('renderKatexHtml: при исключении KaTeX возвращает экранированный <code>', () => {
    jest.spyOn(katex, 'renderToString').mockImplementation(() => {
      throw new Error('bad');
    });
    expect(renderKatexHtml('a<b & c>d', false)).toBe('<code>a&lt;b &amp; c&gt;d</code>');
  });

  it('renderKatexHtml: корректная формула рендерится в html KaTeX', () => {
    expect(renderKatexHtml('x^2', true)).toContain('katex');
  });

  it('preprocessArtifactLatex: валюта со знаком в конце после блока $$ экранируется', () => {
    expect(preprocessArtifactLatex('$$x$$ цена 5$')).toBe('$$x$$ цена 5\\$');
  });

  it('preprocessArtifactLatex: внутри inline-формулы постфикс не трогается, экранированный $ не считается', () => {
    expect(preprocessArtifactLatex('$a = 2$ и \\$ 5$')).toContain('$a = 2$');
  });
});
