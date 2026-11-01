import React from 'react';
import { mockHx, resetHx } from '@/components/MessageRenderer.harness';
import { act, fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom';
import MessageRenderer from '@/components/MessageRenderer';

const RECT = { bottom: 100, left: 40, width: 60, top: 80, right: 100, height: 20, x: 40, y: 80, toJSON: () => ({}) };

function selectNode(node: Node, contents = true) {
  const sel = window.getSelection()!;
  const range = document.createRange();
  if (contents) range.selectNodeContents(node);
  else range.selectNode(node);
  sel.removeAllRanges();
  sel.addRange(range);
  return range;
}

function firstText(el: Element): Node {
  const w = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
  return w.nextNode() as Node;
}

function renderMsg(content: string, onSendMessage: jest.Mock | null = jest.fn()) {
  const utils = render(<MessageRenderer content={content} onSendMessage={onSendMessage ?? undefined} />);
  const root = utils.container.firstElementChild as HTMLElement;
  return { ...utils, root, onSendMessage };
}

async function openMenu(root: HTMLElement, target: Node) {
  selectNode(target);
  fireEvent.mouseUp(root);
  await act(async () => {
    jest.advanceTimersByTime(10);
  });
}

describe('MessageRenderer: выделение текста и меню', () => {
  beforeEach(() => {
    resetHx();
    jest.useFakeTimers();
    (Range.prototype as any).getBoundingClientRect = () => RECT;
  });
  afterEach(() => {
    window.getSelection()?.removeAllRanges();
    jest.useRealTimers();
    jest.restoreAllMocks();
    delete (navigator as any).clipboard;
    delete (global as any).ClipboardItem;
  });

  it('выделение текста внутри сообщения открывает меню действий', async () => {
    const { root } = renderMsg('Привет мир');
    await openMenu(root, root.querySelector('div')!);
    expect(screen.getByText('Копировать')).toBeInTheDocument();
    expect(screen.getByText('Спросить')).toBeInTheDocument();
    expect(screen.getByText('Объяснить')).toBeInTheDocument();
    expect(screen.getByText('Перевести')).toBeInTheDocument();
  });

  it('без onSendMessage меню не включается', async () => {
    const { root } = renderMsg('Привет мир', null);
    selectNode(root.querySelector('div')!);
    fireEvent.mouseUp(root);
    await act(async () => {
      jest.advanceTimersByTime(10);
    });
    expect(screen.queryByText('Копировать')).toBeNull();
  });

  it('пустое выделение, свёрнутое или вне сообщения — меню не показывается', async () => {
    const { root } = renderMsg('Привет мир');
    window.getSelection()!.removeAllRanges();
    fireEvent.mouseUp(root);
    const outside = document.createElement('p');
    outside.textContent = 'снаружи';
    document.body.appendChild(outside);
    selectNode(outside);
    fireEvent.mouseUp(root);
    await act(async () => {
      jest.advanceTimersByTime(10);
    });
    expect(screen.queryByText('Копировать')).toBeNull();
  });

  it('выделение только пробелов не открывает меню', async () => {
    const { root } = renderMsg('a   b');
    const textNode = firstText(root);
    const range = document.createRange();
    range.setStart(textNode, 1);
    range.setEnd(textNode, 4);
    window.getSelection()!.removeAllRanges();
    window.getSelection()!.addRange(range);
    fireEvent.mouseUp(root);
    await act(async () => {
      jest.advanceTimersByTime(10);
    });
    expect(screen.queryByText('Копировать')).toBeNull();
  });

  it('двойной клик по слову тоже открывает меню; без выделения — нет', async () => {
    const { root } = renderMsg('Привет мир');
    window.getSelection()!.removeAllRanges();
    fireEvent.doubleClick(root);
    await act(async () => {
      jest.advanceTimersByTime(10);
    });
    expect(screen.queryByText('Копировать')).toBeNull();
    selectNode(firstText(root), false);
    fireEvent.doubleClick(root);
    await act(async () => {
      jest.advanceTimersByTime(10);
    });
    expect(screen.getByText('Копировать')).toBeInTheDocument();
  });

  it('выделение внутри одного текстового узла: якорь — его родитель (mouseup и двойной клик)', async () => {
    const { root } = renderMsg('Привет мир');
    const text = firstText(root);
    const range = document.createRange();
    range.setStart(text, 0);
    range.setEnd(text, 6);
    window.getSelection()!.removeAllRanges();
    window.getSelection()!.addRange(range);
    fireEvent.mouseUp(root);
    await act(async () => {
      jest.advanceTimersByTime(10);
    });
    expect(screen.getByText('Копировать')).toBeInTheDocument();
    fireEvent.keyDown(document.body, { key: 'Escape' });
    expect(screen.queryByText('Копировать')).toBeNull();
    window.getSelection()!.removeAllRanges();
    window.getSelection()!.addRange(range);
    fireEvent.doubleClick(root);
    await act(async () => {
      jest.advanceTimersByTime(10);
    });
    expect(screen.getByText('Копировать')).toBeInTheDocument();
  });

  it('двойной клик по выделению вне сообщения игнорируется', async () => {
    const { root } = renderMsg('Привет');
    const outside = document.createElement('p');
    outside.textContent = 'снаружи';
    document.body.appendChild(outside);
    selectNode(outside);
    fireEvent.doubleClick(root);
    await act(async () => {
      jest.advanceTimersByTime(10);
    });
    expect(screen.queryByText('Копировать')).toBeNull();
  });

  it('пока меню открыто, повторный mouseup не пересоздаёт его', async () => {
    const { root } = renderMsg('Привет мир');
    await openMenu(root, root.querySelector('div')!);
    fireEvent.mouseUp(root);
    fireEvent.doubleClick(root);
    expect(screen.getAllByText('Копировать')).toHaveLength(1);
  });

  it('«Объяснить» отправляет промпт с выделенным текстом и закрывает меню', async () => {
    const { root, onSendMessage } = renderMsg('Привет мир');
    await openMenu(root, root.querySelector('div')!);
    fireEvent.click(screen.getByText('Объяснить'));
    expect(onSendMessage).toHaveBeenCalledWith(
      expect.stringContaining('Объясни мне эту секцию более подробно'),
    );
    expect(onSendMessage.mock.calls[0][0]).toContain('Привет мир');
    expect(screen.queryByText('Копировать')).toBeNull();
  });

  it('«Спросить» отправляет вопрос с цитатой выделения', async () => {
    const { root, onSendMessage } = renderMsg('Привет мир');
    await openMenu(root, root.querySelector('div')!);
    fireEvent.click(screen.getByText('Спросить'));
    fireEvent.change(screen.getByPlaceholderText('Введите ваш вопрос...'), { target: { value: 'почему?' } });
    fireEvent.keyDown(screen.getByPlaceholderText('Введите ваш вопрос...'), { key: 'Enter' });
    expect(onSendMessage).toHaveBeenCalledWith(expect.stringContaining('почему?'));
    expect(onSendMessage.mock.calls[0][0]).toContain('Привет мир');
  });

  it('«Перевести» отправляет промпт перевода на выбранный язык', async () => {
    const { root, onSendMessage } = renderMsg('Привет мир');
    await openMenu(root, root.querySelector('div')!);
    fireEvent.click(screen.getByText('Перевести'));
    fireEvent.click(screen.getByText('English (United States)'));
    expect(onSendMessage).toHaveBeenCalledWith(expect.stringContaining('English (United States)'));
  });

  it('«Копировать» без ClipboardItem записывает plain-текст', async () => {
    const writeText = jest.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    const { root } = renderMsg('Привет мир');
    await openMenu(root, root.querySelector('div')!);
    await act(async () => {
      fireEvent.click(screen.getByText('Копировать'));
    });
    expect(writeText).toHaveBeenCalledWith('Привет мир');
  });

  it('«Копировать» с ClipboardItem пишет plain + html', async () => {
    const write = jest.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { write, writeText: jest.fn() } });
    const items: Record<string, Blob>[] = [];
    (global as any).ClipboardItem = class {
      constructor(public data: Record<string, Blob>) {
        items.push(data);
      }
    };
    const { root } = renderMsg('Привет **мир**');
    await openMenu(root, root.querySelector('div')!);
    await act(async () => {
      fireEvent.click(screen.getByText('Копировать'));
    });
    expect(write).toHaveBeenCalledTimes(1);
    expect(Object.keys(items[0]).sort()).toEqual(['text/html', 'text/plain']);
  });

  it('сбой копирования логируется', async () => {
    const err = jest.spyOn(console, 'error').mockImplementation(() => {});
    Object.assign(navigator, { clipboard: { writeText: jest.fn().mockRejectedValue(new Error('x')) } });
    const { root } = renderMsg('Привет мир');
    await openMenu(root, root.querySelector('div')!);
    await act(async () => {
      fireEvent.click(screen.getByText('Копировать'));
    });
    expect(err).toHaveBeenCalledWith('Failed to copy:', expect.any(Error));
  });

  it('клик вне меню (после 100 мс) закрывает его; клик по меню — нет', async () => {
    const { root } = renderMsg('Привет мир');
    await openMenu(root, root.querySelector('div')!);
    await act(async () => {
      jest.advanceTimersByTime(150);
    });
    fireEvent.mouseDown(screen.getByText('Копировать'));
    expect(screen.getByText('Копировать')).toBeInTheDocument();
    fireEvent.mouseDown(document.body);
    expect(screen.queryByText('Копировать')).toBeNull();
  });

  it('клик по подменю (список языков) не закрывает меню', async () => {
    const { root } = renderMsg('Привет мир');
    await openMenu(root, root.querySelector('div')!);
    await act(async () => {
      jest.advanceTimersByTime(150);
    });
    fireEvent.click(screen.getByText('Перевести'));
    fireEvent.mouseDown(screen.getByText('German (Germany)'));
    expect(screen.getByText('Копировать')).toBeInTheDocument();
  });

  it('Escape закрывает меню', async () => {
    const { root } = renderMsg('Привет мир');
    await openMenu(root, root.querySelector('div')!);
    fireEvent.keyDown(document.body, { key: 'Escape' });
    expect(screen.queryByText('Копировать')).toBeNull();
  });

  it('якорь выделения — родитель текстового узла, либо сам контейнер', async () => {
    const { root } = renderMsg('Привет мир');
    const textNode = firstText(root);
    selectNode(textNode, false);
    fireEvent.mouseUp(root);
    await act(async () => {
      jest.advanceTimersByTime(10);
    });
    expect(screen.getByText('Копировать')).toBeInTheDocument();
    // позиция меню берётся из rect выделения
    const menu = document.querySelector('[data-menu="code-selection"]') as HTMLElement;
    expect(getComputedStyle(menu).top).toBe('108px');
    expect(getComputedStyle(menu).left).toBe('70px');
  });
});

describe('MessageRenderer: копирование выделенного (onCopy)', () => {
  beforeEach(() => {
    resetHx();
    (Range.prototype as any).getBoundingClientRect = () => RECT;
  });
  afterEach(() => {
    window.getSelection()?.removeAllRanges();
  });

  function copyAll(content: string) {
    const { root } = renderMsg(content, null);
    selectNode(root);
    const data: Record<string, string> = {};
    const prevented = { v: false };
    const evt = new Event('copy', { bubbles: true, cancelable: true }) as any;
    evt.clipboardData = { setData: (t: string, v: string) => (data[t] = v) };
    act(() => {
      root.dispatchEvent(evt);
    });
    prevented.v = evt.defaultPrevented;
    return { data, prevented: prevented.v, root };
  }

  it('без выделения копирование не перехватывается', () => {
    const { root } = renderMsg('Привет', null);
    window.getSelection()!.removeAllRanges();
    const evt = new Event('copy', { bubbles: true, cancelable: true }) as any;
    evt.clipboardData = { setData: jest.fn() };
    root.dispatchEvent(evt);
    expect(evt.defaultPrevented).toBe(false);
    expect(evt.clipboardData.setData).not.toHaveBeenCalled();
  });

  it('копирование выделенного пишет text/plain и text/html (с обёрткой фрагмента для Word)', () => {
    const { data, prevented } = copyAll('Привет **мир**');
    expect(prevented).toBe(true);
    expect(data['text/plain']).toBe('Привет мир');
    expect(data['text/html']).toContain('<!--StartFragment-->');
    expect(data['text/html']).toContain('<strong>мир</strong>');
  });

  it('заголовки, списки, цитаты, таблицы, код сериализуются осмысленно', () => {
    const md =
      '# Заголовок\n\n- один\n- два\n\n3. третий\n\n> цитата\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\nтекст `код` и *курсив*\n\n---\n\n```js\nvar x;\n```';
    const { data } = copyAll(md);
    const plain = data['text/plain'];
    expect(plain).toContain('Заголовок');
    expect(plain).toContain('• один');
    expect(plain).toContain('• два');
    expect(plain).toContain('третий');
    expect(plain).toContain('цитата');
    expect(plain).toContain('a\tb');
    expect(plain).toContain('1\t2');
    expect(plain).toContain('код');
    const html = data['text/html'];
    expect(html).toContain('<h1>');
    expect(html).toContain('<ul>');
    expect(html).toContain('<li>');
    expect(html).toContain('<table>');
    expect(html).toContain('<code>код</code>');
    expect(html).toContain('<em>курсив</em>');
  });

  function copyInjected(html: string) {
    const { root } = renderMsg('текст', null);
    const injected = document.createElement('div');
    injected.innerHTML = html;
    root.appendChild(injected);
    selectNode(injected);
    const out: Record<string, string> = {};
    const evt = new Event('copy', { bubbles: true, cancelable: true }) as any;
    evt.clipboardData = { setData: (t: string, v: string) => (out[t] = v) };
    act(() => {
      root.dispatchEvent(evt);
    });
    return out;
  }

  it('блок <pre> копируется с переводом строки и моноширинным стилем', () => {
    const out = copyInjected('<pre>a\nb</pre>');
    expect(out['text/plain']).toBe('a\nb');
    expect(out['text/html']).toContain('<pre style="white-space:pre-wrap');
  });

  it('<blockquote> и вложенный в обёртки <li> определяют тип списка по ближайшему предку', () => {
    const out = copyInjected('<blockquote>цитата</blockquote><section><ul><div><li>пункт</li></div></ul></section>');
    expect(out['text/html']).toContain('<blockquote>цитата</blockquote>');
    expect(out['text/plain']).toContain('• пункт');
  });

  it('выделение без текста (картинка): копирование не перехватывается', () => {
    const out = copyInjected('<img alt="x" src="https://e.org/a.png">');
    expect(out['text/plain']).toBeUndefined();
  });

  it('(known issue) нумерованный markdown-список копируется с маркером «•» и слипшимся номером', () => {
    // Правильное поведение: «3. третий». Сейчас у <li> нет атрибута value, поэтому берётся маркер «• »,
    // а номер из отдельного <span> склеивается с текстом.
    const { data } = copyAll('3. третий');
    expect(data['text/plain']).toBe('• 3.третий');
  });

  it('номер элемента <ol><li value> сохраняется при копировании', () => {
    const { root } = renderMsg('текст', null);
    const injected = document.createElement('div');
    injected.innerHTML = '<ol><li value="7">семь</li></ol><ul><li>пункт</li></ul>';
    root.appendChild(injected);
    selectNode(injected);
    const out: Record<string, string> = {};
    const evt = new Event('copy', { bubbles: true, cancelable: true }) as any;
    evt.clipboardData = { setData: (t: string, v: string) => (out[t] = v) };
    act(() => {
      root.dispatchEvent(evt);
    });
    expect(out['text/plain']).toContain('7. семь');
    expect(out['text/plain']).toContain('• пункт');
  });

  it('скрытые style/script и номера строк редактора не попадают в копию', () => {
    const { root } = renderMsg('```js\nvar a;\n```', null);
    const injected = document.createElement('div');
    injected.innerHTML = '<style>.x{}</style><script>1</script><span class="margin-view-overlays">1</span>видимое';
    root.appendChild(injected);
    selectNode(injected);
    const out: Record<string, string> = {};
    const evt = new Event('copy', { bubbles: true, cancelable: true }) as any;
    evt.clipboardData = { setData: (t: string, v: string) => (out[t] = v) };
    act(() => {
      root.dispatchEvent(evt);
    });
    expect(out['text/plain']).toBe('видимое');
    expect(out['text/html']).not.toContain('<style');
    expect(out['text/html']).not.toContain('margin-view');
  });

  it('копирование не падает, если getComputedStyle бросает исключение', () => {
    const spy = jest.spyOn(window, 'getComputedStyle').mockImplementation(() => {
      throw new Error('no style');
    });
    const { root } = renderMsg('**жирный** и <i>курсив</i>', null);
    selectNode(root);
    const out: Record<string, string> = {};
    const evt = new Event('copy', { bubbles: true, cancelable: true }) as any;
    evt.clipboardData = { setData: (t: string, v: string) => (out[t] = v) };
    act(() => {
      root.dispatchEvent(evt);
    });
    spy.mockRestore();
    expect(out['text/plain']).toContain('жирный');
    expect(out['text/html']).toContain('<strong>жирный</strong>');
    expect(out['text/html']).toContain('<em>курсив</em>');
  });

  it('временный узел копирования удаляется из документа', () => {
    copyAll('Привет **мир**');
    expect(document.querySelector('[data-astra-copy-root]')).toBeNull();
  });
});
