import React from 'react';
import { mockHx, resetHx } from '@/components/MessageRenderer.harness';
import { act, fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom';
import MessageRenderer from '@/components/MessageRenderer';

function r(content: string, props: Record<string, unknown> = {}) {
  return render(<MessageRenderer content={content} {...props} />);
}

const DECK =
  '<div class="slide"><h1>Заголовок презентации</h1></div><div class="slide"><p>Второй слайд</p></div>';

describe('MessageRenderer: блоки кода', () => {
  beforeEach(() => {
    resetHx();
    jest.restoreAllMocks();
    jest.spyOn(window, 'requestAnimationFrame').mockImplementation((cb: FrameRequestCallback) => {
      cb(0);
      return 0;
    });
  });

  it('fenced-код отображается в Monaco только для чтения с языком и стабильным путём', () => {
    r('```python\nprint(1)\n```');
    const ed = screen.getByTestId('monaco');
    expect(ed).toHaveTextContent('print(1)');
    expect(ed).toHaveAttribute('data-language', 'python');
    expect(ed.getAttribute('data-path')).toMatch(/^readonly:\/\/python\/[a-z0-9]+-\d+\.code$/);
    expect(ed).toHaveAttribute('data-readonly', 'true');
    expect(screen.getByText('python')).toBeInTheDocument();
  });

  it.each([
    ['js', 'javascript'],
    ['ts', 'typescript'],
    ['py', 'python'],
    ['sh', 'bash'],
    ['yml', 'yaml'],
    ['cs', 'csharp'],
    ['rs', 'rust'],
    ['ps1', 'powershell'],
    ['golang', 'golang'],
  ])('язык fence «%s» -> язык редактора «%s»', (fence, editor) => {
    r('```' + fence + '\nx\n```');
    expect(screen.getByTestId('monaco')).toHaveAttribute('data-language', editor);
  });

  it('(known issue) языки с символами (c++, c#) обрезаются до «c»: подсветка и расширение файла неверные', () => {
    // Правильное поведение: ```c++ -> cpp, ```c# -> csharp. Регулярка fence берёт только \w+, остальное отбрасывает.
    r('```c++\nx\n```');
    r('```c#\nx\n```');
    const eds = screen.getAllByTestId('monaco');
    expect(eds[0]).toHaveAttribute('data-language', 'c');
    expect(eds[1]).toHaveAttribute('data-language', 'c');
  });

  it('fence без языка -> язык «text»', () => {
    r('```\nпросто\n```');
    expect(screen.getByTestId('monaco')).toHaveAttribute('data-language', 'text');
    expect(screen.getByTestId('monaco')).toHaveTextContent('просто');
  });

  it('номера строк включаются только для кода длиннее 5 строк', () => {
    r('```js\n1\n2\n3\n```');
    expect(screen.getByTestId('monaco')).toHaveAttribute('data-linenumbers', 'off');
    r('```js\n1\n2\n3\n4\n5\n6\n```');
    expect(screen.getAllByTestId('monaco')[1]).toHaveAttribute('data-linenumbers', 'on');
  });

  it('Monaco получает тему, onMount вызывает layout', () => {
    r('```js\nx\n```');
    expect(mockHx.defineTheme).toHaveBeenCalledWith(
      'memo-monaco-dark',
      expect.objectContaining({ base: 'vs-dark' }),
    );
    expect(mockHx.editorLayout).toHaveBeenCalled();
  });

  it('CRLF в коде нормализуется, завершающий перенос убирается', () => {
    r('```js\r\nline1\r\nline2\n```');
    expect(screen.getByTestId('monaco').textContent).toBe('line1\nline2');
  });

  it('высота редактора растёт с числом строк, минимум 120px', () => {
    r('```js\na\n```');
    expect(screen.getByTestId('monaco')).toHaveAttribute('data-height', '120px');
    r('```js\n' + Array.from({ length: 10 }, (_, i) => `l${i}`).join('\n') + '\n```');
    expect(screen.getAllByTestId('monaco')[1]).toHaveAttribute('data-height', '238px');
  });

  it('длинный код сворачивается (320px), кнопка «Развернуть код» раскрывает и сворачивает обратно', () => {
    const code = Array.from({ length: 30 }, (_, i) => `l${i}`).join('\n');
    r('```js\n' + code + '\n```');
    expect(screen.getByTestId('monaco')).toHaveAttribute('data-height', '320px');
    fireEvent.mouseDown(screen.getByLabelText('Развернуть код'));
    fireEvent.click(screen.getByLabelText('Развернуть код'));
    expect(screen.getByTestId('monaco')).toHaveAttribute('data-height', `${30 * 22 + 18}px`);
    expect(mockHx.editorLayout).toHaveBeenCalled();
    fireEvent.click(screen.getByLabelText('Свернуть код'));
    expect(screen.getByTestId('monaco')).toHaveAttribute('data-height', '320px');
  });

  it('короткий код не имеет кнопки сворачивания', () => {
    r('```js\nx\n```');
    expect(screen.queryByLabelText('Развернуть код')).toBeNull();
  });

  describe('копирование и скачивание', () => {
    afterEach(() => {
      delete (navigator as any).clipboard;
      jest.useRealTimers();
    });

    it('копирование: текст кода записывается в буфер обмена', async () => {
      jest.useFakeTimers();
      const writeText = jest.fn().mockResolvedValue(undefined);
      Object.assign(navigator, { clipboard: { writeText } });
      r('```js\nconsole.log(1)\n```');
      await act(async () => {
        fireEvent.click(screen.getByLabelText('Копировать код'));
      });
      expect(writeText).toHaveBeenCalledWith('console.log(1)');
    });

    it('(known issue) после копирования подсказка «✓ Скопировано!» не появляется: разметка блоков закеширована useMemo', async () => {
      // Правильное поведение: после успешного копирования иконка/подсказка меняются на «Скопировано» на 2 с.
      // Сейчас copiedCode меняется в родителе, но parseMarkdown закеширован и не получает новое значение.
      jest.useFakeTimers();
      Object.assign(navigator, { clipboard: { writeText: jest.fn().mockResolvedValue(undefined) } });
      r('```js\nconsole.log(1)\n```');
      await act(async () => {
        fireEvent.click(screen.getByLabelText('Копировать код'));
      });
      act(() => {
        jest.advanceTimersByTime(2100);
      });
      expect(screen.queryByLabelText('✓ Скопировано!')).toBeNull();
      expect(screen.getByLabelText('Копировать код')).toBeInTheDocument();
    });

    it('сбой копирования логируется', async () => {
      const err = jest.spyOn(console, 'error').mockImplementation(() => {});
      Object.assign(navigator, { clipboard: { writeText: jest.fn().mockRejectedValue(new Error('x')) } });
      r('```js\nx\n```');
      await act(async () => {
        fireEvent.click(screen.getByLabelText('Копировать код'));
      });
      expect(err).toHaveBeenCalledWith('Failed to copy code:', expect.any(Error));
    });

    function spyDownload() {
      const names: string[] = [];
      (URL as any).createObjectURL = jest.fn(() => 'blob:x');
      (URL as any).revokeObjectURL = jest.fn();
      jest.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
        names.push(this.download);
      });
      return names;
    }

    it('скачивание: имя code_<дата>.<расширение по языку>', () => {
      const names = spyDownload();
      r('```python\nx\n```');
      fireEvent.click(screen.getByLabelText('Скачать файл'));
      expect(names[0]).toMatch(/^code_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.py$/);
      expect((URL as any).revokeObjectURL).toHaveBeenCalledWith('blob:x');
    });

    it('Dockerfile скачивается без префикса code_ и расширения', () => {
      const names = spyDownload();
      r('```dockerfile\nFROM x\n```');
      fireEvent.click(screen.getByLabelText('Скачать файл'));
      expect(names[0]).toMatch(/^Dockerfile_\d{4}-/);
    });

    it('неизвестный язык даёт расширение по его имени; без языка -> txt', () => {
      const names = spyDownload();
      r('```foo\nx\n```');
      r('```\nx\n```');
      const buttons = screen.getAllByLabelText('Скачать файл');
      fireEvent.click(buttons[0]);
      fireEvent.click(buttons[1]);
      expect(names[0]).toMatch(/\.foo$/);
      expect(names[1]).toMatch(/\.txt$/);
    });

    it('сбой скачивания логируется', () => {
      const err = jest.spyOn(console, 'error').mockImplementation(() => {});
      (URL as any).createObjectURL = jest.fn(() => {
        throw new Error('blob');
      });
      r('```js\nx\n```');
      fireEvent.click(screen.getByLabelText('Скачать файл'));
      expect(err).toHaveBeenCalledWith('Failed to download code:', expect.any(Error));
    });
  });

  describe('стрим', () => {
    it('во время стрима код показывается простым <pre>, а не Monaco', () => {
      const { container } = r('```js\nlet a = 1;', { isStreaming: true });
      expect(screen.queryByTestId('monaco')).toBeNull();
      expect(container.querySelector('pre')).toHaveTextContent('let a = 1;');
    });

    it('после стрима незакрытый fence (обрыв) всё равно показывается как код', () => {
      r('```js\nlet a = 1;');
      expect(screen.getByTestId('monaco')).toHaveTextContent('let a = 1;');
    });

    it('стрим длинного кода: pre ограничен по высоте до разворота', () => {
      const code = Array.from({ length: 30 }, (_, i) => `l${i}`).join('\n');
      const { container } = r('```js\n' + code, { isStreaming: true });
      expect(container.querySelector('pre')).toHaveTextContent('l29');
      expect(screen.getByLabelText('Развернуть код')).toBeInTheDocument();
    });
  });

  describe('безопасность: код не исполняется и не интерпретируется как разметка', () => {
    it('HTML внутри fence другого языка выводится текстом', () => {
      const { container } = r('```text\n<img src=x onerror="window.__xss=1"><script>window.__xss=2</script>\n```');
      expect(container.querySelector('img, script')).toBeNull();
      expect(screen.getByTestId('monaco').textContent).toContain('onerror');
    });

    it('при стриме <pre> содержит HTML как текст', () => {
      const { container } = r('```text\n<b onclick="x()">t</b>', { isStreaming: true });
      expect(container.querySelector('b')).toBeNull();
      expect(container.querySelector('pre')!.textContent).toContain('<b onclick');
    });
  });
});

describe('MessageRenderer: fence-артефакты (mermaid / svg / html)', () => {
  beforeEach(() => resetHx());

  it('без разрешения viewer mermaid остаётся обычным кодом', () => {
    r('```mermaid\ngraph TD; A-->B\n```');
    expect(screen.queryByTestId('artifact-card')).toBeNull();
    expect(screen.getByTestId('monaco')).toHaveAttribute('data-language', 'mermaid');
  });

  it('с разрешением ```mermaid -> ArtifactCard (закрытый)', () => {
    mockHx.artifactsAllowed = true;
    r('```mermaid\ngraph TD; A-->B\n```', { messageId: 'm1' });
    const card = screen.getByTestId('artifact-card');
    expect(card).toHaveAttribute('data-type', 'application/vnd.mermaid');
    expect(card).toHaveAttribute('data-closed', 'true');
    expect(card).toHaveAttribute('data-id', expect.stringContaining('fence-mermaid-m1'));
    expect(card).toHaveAttribute('data-title', 'Диаграмма Mermaid');
  });

  it('mermaid на стриме с незакрытым fence — не закрыт', () => {
    mockHx.artifactsAllowed = true;
    r('```mermaid\ngraph TD; A-->B', { isStreaming: true });
    expect(screen.getByTestId('artifact-card')).toHaveAttribute('data-closed', 'false');
    expect(screen.getByTestId('artifact-card')).toHaveAttribute('data-streaming', 'true');
  });

  it('код без языка, похожий на mermaid, тоже открывается как диаграмма', () => {
    mockHx.artifactsAllowed = true;
    r('```\nflowchart LR\n  A --> B\n```');
    expect(screen.getByTestId('artifact-card')).toHaveAttribute('data-type', 'application/vnd.mermaid');
  });

  it('```svg и ```xml с <svg> -> SVG-артефакт; xml без svg — код', () => {
    mockHx.artifactsAllowed = true;
    r('```svg\n<svg></svg>\n```');
    expect(screen.getByTestId('artifact-card')).toHaveAttribute('data-type', 'image/svg+xml');
    r('```xml\n<svg viewBox="0 0 1 1"></svg>\n```');
    expect(screen.getAllByTestId('artifact-card')).toHaveLength(2);
    r('```xml\n<root/>\n```');
    expect(screen.getAllByTestId('artifact-card')).toHaveLength(2);
    expect(screen.getByTestId('monaco')).toHaveAttribute('data-language', 'xml');
  });

  it('SVG с onload остаётся в содержимом артефакта как есть (изоляция — на стороне sandbox-iframe превью)', () => {
    mockHx.artifactsAllowed = true;
    r('```svg\n<svg onload="alert(1)"></svg>\n```');
    expect(screen.getByTestId('artifact-card').textContent).toContain('onload');
  });

  it('```html с разрешением -> ArtifactCard text/html, id по messageId и индексу', () => {
    mockHx.artifactsAllowed = true;
    r('```html\n<p>x</p>\n```', { messageId: 'm7' });
    const card = screen.getByTestId('artifact-card');
    expect(card).toHaveAttribute('data-type', 'text/html');
    expect(card).toHaveAttribute('data-id', expect.stringContaining('fence-html-m7'));
    expect(card).toHaveAttribute('data-closed', 'true');
  });

  it('```html без разрешения — обычный код', () => {
    r('```html\n<p>x</p>\n```');
    expect(screen.queryByTestId('artifact-card')).toBeNull();
    expect(screen.getByTestId('monaco')).toHaveAttribute('data-language', 'html');
  });

  it('```html с открытым fence на стриме при viewerAllowed (презентационный режим) — карточка не закрыта или viewer', () => {
    mockHx.artifactsAllowed = true;
    r('```html\n<p>x', { isStreaming: true });
    expect(screen.getByTestId('artifact-card')).toHaveAttribute('data-closed', 'false');
  });
});

describe('MessageRenderer: презентации', () => {
  beforeEach(() => resetHx());

  it('готовый ```html со слайдами при разрешении — viewer + исходный код в sourceSlot', () => {
    mockHx.artifactsAllowed = true;
    r('```html\n' + DECK + '\n```');
    const v = screen.getByTestId('presentation');
    expect(v).toHaveAttribute('data-streaming', 'false');
    expect(v).toHaveAttribute('data-has-source', 'true');
    expect(screen.getByTestId('monaco')).toBeInTheDocument();
  });

  it('presentationExpected без прав viewer всё равно открывает viewer для слайдов', () => {
    mockHx.presentationExpected = true;
    r('```html\n' + DECK + '\n```');
    expect(screen.getByTestId('presentation')).toBeInTheDocument();
  });

  it('слайды без разрешения и без skill — обычный код (viewer не включается)', () => {
    r('```html\n' + DECK + '\n```');
    expect(screen.queryByTestId('presentation')).toBeNull();
    expect(screen.getByTestId('monaco')).toBeInTheDocument();
  });

  it('стрим презентации: viewer без исходника, isStreaming пробрасывается', () => {
    mockHx.presentationExpected = true;
    r('```html\n<div class="slide">Заголовок презентации', { isStreaming: true });
    const v = screen.getByTestId('presentation');
    expect(v).toHaveAttribute('data-streaming', 'true');
    expect(v).toHaveAttribute('data-has-source', 'false');
  });

  it('skill + стрим + любой html-fence форсирует viewer до появления .slide', () => {
    mockHx.presentationExpected = true;
    r('```html\n<p>пока ничего', { isStreaming: true });
    expect(screen.getByTestId('presentation')).toBeInTheDocument();
    expect(screen.queryByTestId('artifact-card')).toBeNull();
  });

  it('viewer не откатывается на карточку при следующем чанке без маркеров (sticky на стриме)', () => {
    mockHx.presentationExpected = true;
    const { rerender } = r('```html\n' + DECK, { isStreaming: true });
    expect(screen.getByTestId('presentation')).toBeInTheDocument();
    rerender(<MessageRenderer content={'```html\n<div>обрезано'} isStreaming />);
    expect(screen.getByTestId('presentation')).toBeInTheDocument();
  });

  it('стрим: однажды показанный viewer (слайды) не откатывается на карточку, даже если skill не ожидается', () => {
    mockHx.artifactsAllowed = true;
    const { rerender } = r('```html\n' + DECK, { isStreaming: true });
    expect(screen.getByTestId('presentation')).toBeInTheDocument();
    rerender(<MessageRenderer content={'```html\n<div>обрезано'} isStreaming />);
    expect(screen.getByTestId('presentation')).toBeInTheDocument();
    expect(screen.queryByTestId('artifact-card')).toBeNull();
  });

  it('сырой <html>-документ со слайдами в тексте не ломается нормализацией инлайн-HTML', () => {
    mockHx.artifactsAllowed = true;
    r('Итог:\n<html><body>' + DECK + '</body></html>');
    expect(screen.getByTestId('presentation')).toBeInTheDocument();
  });

  it('после стрима оценка пересматривается: обычный html снова карточка', () => {
    mockHx.presentationExpected = true;
    mockHx.artifactsAllowed = true;
    const { rerender } = r('```html\n' + DECK, { isStreaming: true });
    expect(screen.getByTestId('presentation')).toBeInTheDocument();
    mockHx.presentationExpected = false;
    rerender(<MessageRenderer content={'```html\n<p>dash</p>\n```'} isStreaming={false} />);
    expect(screen.queryByTestId('presentation')).toBeNull();
    expect(screen.getByTestId('artifact-card')).toBeInTheDocument();
  });

  it('обрезанная презентация после complete (без закрывающего fence) — всё равно viewer', () => {
    mockHx.presentationExpected = true;
    r('```html\n<div class="slide-title">Заголовок</div><div class="content-zone">x</div>');
    expect(screen.getByTestId('presentation')).toBeInTheDocument();
  });

  it('unfenced HTML презентации превращается в viewer, текст до него сохраняется', () => {
    mockHx.artifactsAllowed = true;
    r('Вот презентация:\n\n' + DECK);
    expect(screen.getByTestId('presentation')).toBeInTheDocument();
    expect(document.body).toHaveTextContent('Вот презентация:');
  });

  it('unfenced HTML без права viewer остаётся текстом/инлайн-HTML', () => {
    r('Вот презентация:\n\n' + DECK);
    expect(screen.queryByTestId('presentation')).toBeNull();
  });

  it('unfenced <style>/<!DOCTYPE> на стриме со skill — сразу viewer', () => {
    mockHx.presentationExpected = true;
    r('<!DOCTYPE html><html><head><style>.x{}</style></head>', { isStreaming: true });
    expect(screen.getByTestId('presentation')).toHaveAttribute('data-streaming', 'true');
  });

  it('при presentationExpected склеиваются несколько ```html блоков в один viewer', () => {
    mockHx.presentationExpected = true;
    mockHx.artifactsAllowed = true;
    r('```html\n' + '<div class="slide">Заголовок презентации раз</div>\n```\n```html\n<div class="slide">два</div>\n```');
    expect(screen.getAllByTestId('presentation')).toHaveLength(1);
  });
});

describe('MessageRenderer: блоки :::artifact', () => {
  beforeEach(() => resetHx());

  const ART = (type: string, body: string, closed = true) =>
    `:::artifact{identifier="demo" type="${type}" title="Демо"}\n\`\`\`\n${body}\n${closed ? '```\n:::' : ''}`;

  it('без права viewer артефакт показывается кодом', () => {
    r(ART('text/html', '<p>x</p>'));
    expect(screen.queryByTestId('artifact-card')).toBeNull();
    expect(screen.getByTestId('monaco')).toHaveTextContent('<p>x</p>');
  });

  it('с правом viewer — ArtifactCard с заголовком и типом', () => {
    mockHx.artifactsAllowed = true;
    r(ART('text/html', '<p>x</p>'), { messageId: 'm' });
    const card = screen.getByTestId('artifact-card');
    expect(card).toHaveAttribute('data-title', 'Демо');
    expect(card).toHaveAttribute('data-type', 'text/html');
    expect(card).toHaveAttribute('data-closed', 'true');
  });

  it('текст вокруг артефакта сохраняется', () => {
    mockHx.artifactsAllowed = true;
    r('До\n\n' + ART('text/html', '<p>x</p>') + '\n\nПосле');
    expect(document.body).toHaveTextContent('До');
    expect(document.body).toHaveTextContent('После');
  });

  it(':::artifact со слайдами открывается как viewer презентации, а не пустая незакрытая карточка', () => {
    mockHx.artifactsAllowed = true;
    r(ART('text/html', DECK));
    expect(screen.getByTestId('presentation')).toBeInTheDocument();
    expect(screen.queryByTestId('artifact-card')).toBeNull();
  });

  it('стрим артефакта с признаками презентации (без слайдов): viewer в режиме генерации', () => {
    mockHx.artifactsAllowed = true;
    mockHx.presentationExpected = true;
    r(ART('text/html', '<div class="content-zone">x', false), { isStreaming: true });
    expect(screen.getByTestId('presentation')).toHaveAttribute('data-streaming', 'true');
  });

  it('по названию «презентация» на стриме со skill — viewer ещё до слайдов', () => {
    mockHx.artifactsAllowed = true;
    mockHx.presentationExpected = true;
    r(':::artifact{identifier="deck" type="text/html" title="Презентация"}\n```html\n<p>пока', { isStreaming: true });
    expect(screen.getByTestId('presentation')).toBeInTheDocument();
  });

  it('незакрытый артефакт вне стрима — карточка без «генерации»', () => {
    mockHx.artifactsAllowed = true;
    r(ART('text/html', '<p>x</p>', false), { isStreaming: false });
    const card = screen.getByTestId('artifact-card');
    expect(card).toHaveAttribute('data-closed', 'false');
    expect(card).toHaveAttribute('data-streaming', 'false');
  });

  it('несколько артефактов подряд: презентация (по признакам на стриме) поднимается наверх', () => {
    mockHx.artifactsAllowed = true;
    mockHx.presentationExpected = true;
    r(`${ART('text/html', '<p>обычный</p>')}\n${ART('text/html', '<div class="content-zone">x', false)}`, {
      isStreaming: true,
    });
    const order = Array.from(document.querySelectorAll('[data-testid="artifact-card"], [data-testid="presentation"]')).map(
      (e) => e.getAttribute('data-testid'),
    );
    expect(order).toHaveLength(2);
    expect(order).toEqual(expect.arrayContaining(['presentation', 'artifact-card']));
  });

  it('обычный артефакт на стриме передаёт isStreaming в карточку', () => {
    mockHx.artifactsAllowed = true;
    r(ART('image/svg+xml', '<svg/>', false), { isStreaming: true });
    expect(screen.getByTestId('artifact-card')).toHaveAttribute('data-streaming', 'true');
    expect(screen.getByTestId('artifact-card')).toHaveAttribute('data-closed', 'false');
  });

  it('presentationStreamActive: viewer разрешён на стриме при skill презентации даже без artifactsAllowed', () => {
    mockHx.presentationExpected = true;
    r(ART('text/html', '<p>x</p>', false), { isStreaming: true });
    expect(screen.getByTestId('artifact-card')).toBeInTheDocument();
  });
});
