/**
 * Самодостаточные регрессионные тесты MessageRenderer (BUG-FE-53/54/55/56).
 * Не зависят от MessageRenderer.harness: все тяжёлые зависимости замокированы здесь же.
 */
import React from 'react';
import { render, screen } from '@testing-library/react';
import '@testing-library/jest-dom';
import MessageRenderer from '@/components/MessageRenderer';

const gate = { allowed: false, expected: false };

jest.mock('@monaco-editor/react', () => ({
  __esModule: true,
  default: ({ value }: { value?: string }) => <pre data-testid="monaco">{value}</pre>,
  loader: { config: jest.fn(), init: jest.fn(() => Promise.resolve()) },
}));

jest.mock('@/contexts/AppContext', () => ({
  useAppContext: () => ({ state: { currentChatId: 'chat-1' } }),
}));

jest.mock('@/hooks/useChatFontSize', () => ({
  useChatFontSize: () => ({ fontSize: 'medium', fontSizeValue: '14px' }),
}));

jest.mock('@/hooks/useArtifactsViewerGate', () => ({
  useArtifactsViewerAllowed: () => gate.allowed,
  useArtifactsViewerLiveGate: () => false,
  usePresentationViewerExpected: () => gate.expected,
}));

jest.mock('@/components/artifacts/ArtifactCard', () => ({
  __esModule: true,
  default: () => <div data-testid="artifact-card" />,
}));

jest.mock('@/components/InlinePresentationViewer', () => ({
  __esModule: true,
  default: () => <div data-testid="presentation" />,
}));

const DECK =
  '<div class="slide"><h1>Заголовок презентации</h1></div><div class="slide"><p>Второй слайд</p></div>';
const ART = (type: string, body: string) =>
  `:::artifact{identifier="demo" type="${type}" title="Демо"}\n\`\`\`\n${body}\n\`\`\`\n:::`;

function r(content: string) {
  return render(<MessageRenderer content={content} />);
}

describe('MessageRenderer: исправления markdown', () => {
  beforeEach(() => {
    gate.allowed = false;
    gate.expected = false;
    localStorage.clear();
  });

  describe('BUG-FE-53: плейсхолдеры HTML-блоков', () => {
    it('<ul>/<pre> рендерятся блоками, а не текстом «ASTRAHTMLBLOCK»', () => {
      const { container } = r('<ul><li>раз</li><li>два</li></ul>\n\n<pre>код</pre>');
      expect(container.querySelectorAll('li')).toHaveLength(2);
      expect(container.textContent).not.toContain('ASTRA');
      expect(container.textContent).toContain('раз');
      expect(container.textContent).toContain('код');
    });

    it('несколько HTML-блоков подряд сохраняются по порядку', () => {
      const { container } = r('<ul><li>a1</li></ul>\n\n<ul><li>b2</li></ul>\n\n<pre>c3</pre>');
      expect(container.textContent).not.toContain('ASTRA');
      expect(container.textContent).toMatch(/a1[\s\S]*b2[\s\S]*c3/);
    });

    it('обычный текст с подчёркиваниями (snake_case, __жирный__) работает как раньше', () => {
      const { container } = r('df_date и __жирный__');
      expect(container.textContent).toContain('df');
      expect(container.querySelector('strong')).toHaveTextContent('жирный');
    });
  });

  describe('BUG-FE-54: строка после списка', () => {
    it('абзац сразу после списка не пропадает', () => {
      const { container } = r('- a\n- b\nтекст после');
      expect(container.querySelectorAll('ul')).toHaveLength(1);
      expect(container.querySelectorAll('li')).toHaveLength(2);
      expect(container).toHaveTextContent('текст после');
    });

    it('нумерованный список, затем абзац без пустой строки', () => {
      const { container } = r('1. один\n2. два\nитог');
      expect(container.querySelectorAll('ol')).toHaveLength(1);
      expect(container).toHaveTextContent('итог');
    });

    it('пустая строка после списка по-прежнему закрывает его без лишних элементов', () => {
      const { container } = r('- a\n- b\n\nтекст после');
      expect(container.querySelectorAll('ul')).toHaveLength(1);
      expect(container).toHaveTextContent('текст после');
    });

    it('заголовок сразу после списка тоже выводится', () => {
      const { container } = r('- a\n# Заголовок');
      expect(container.querySelectorAll('ul')).toHaveLength(1);
      expect(container.querySelector('h1')).toHaveTextContent('Заголовок');
    });
  });

  describe('BUG-FE-55: :::artifact со слайдами', () => {
    it('артефакт-презентация открывается viewer-ом, а не пустой карточкой', () => {
      gate.allowed = true;
      r(ART('text/html', DECK));
      expect(screen.getByTestId('presentation')).toBeInTheDocument();
      expect(screen.queryByTestId('artifact-card')).toBeNull();
    });

    it('текст вокруг артефакта сохраняется', () => {
      gate.allowed = true;
      r('До\n\n' + ART('text/html', DECK) + '\n\nПосле');
      expect(document.body).toHaveTextContent('До');
      expect(document.body).toHaveTextContent('После');
      expect(screen.getByTestId('presentation')).toBeInTheDocument();
    });
  });

  describe('BUG-FE-56: markdown-картинки', () => {
    it('![alt](url) становится <img>, а не ссылкой с «!»', () => {
      const { container } = r('![схема](https://example.com/a.png)');
      const img = container.querySelector('img')!;
      expect(img).not.toBeNull();
      expect(img).toHaveAttribute('src', 'https://example.com/a.png');
      expect(img).toHaveAttribute('alt', 'схема');
      expect(container.querySelector('a')).toBeNull();
      expect(container.textContent).not.toContain('!');
    });

    it('обычная ссылка рядом с картинкой остаётся ссылкой', () => {
      const { container } = r('[сайт](https://example.com/x) ![pic](https://example.com/p.png)');
      expect(container.querySelectorAll('a')).toHaveLength(1);
      expect(container.querySelectorAll('img')).toHaveLength(1);
    });
  });
});
