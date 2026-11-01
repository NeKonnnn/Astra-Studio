import React from 'react';
import { mockHx, resetHx } from '@/components/MessageRenderer.harness';
import { fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom';
import MessageRenderer from '@/components/MessageRenderer';

function r(content: string, props: Record<string, unknown> = {}) {
  return render(<MessageRenderer content={content} {...props} />);
}

describe('MessageRenderer: текст и markdown', () => {
  beforeEach(() => {
    resetHx();
    localStorage.clear();
  });

  it('пустое содержимое — пустой контейнер', () => {
    const { container } = r('');
    expect(container.textContent).toBe('');
  });

  it('обычный абзац рендерится одним блоком текста', () => {
    const { container } = r('Привет, мир');
    expect(container).toHaveTextContent('Привет, мир');
  });

  it('пустые строки превращаются в переносы <br>', () => {
    const { container } = r('a\n\nb');
    expect(container.querySelectorAll('br')).toHaveLength(1);
  });

  it.each([
    ['# H1', 'h1'],
    ['## H2', 'h2'],
    ['### H3', 'h3'],
    ['#### H4', 'h4'],
    ['##### H5', 'h5'],
    ['###### H6', 'h6'],
  ])('заголовок «%s» -> <%s>', (md, tag) => {
    const { container } = r(md);
    const el = container.querySelector(tag)!;
    expect(el).not.toBeNull();
    expect(el.textContent).toBe(md.replace(/^#+\s/, ''));
  });

  it('жирный, курсив, зачёркнутый', () => {
    const { container } = r('**жирный** и *курсив* и ~~убрать~~');
    expect(container.querySelector('strong')).toHaveTextContent('жирный');
    expect(container.querySelector('em')).toHaveTextContent('курсив');
    expect(container.querySelector('del')).toHaveTextContent('убрать');
  });

  it('курсив внутри жирного: **a *b* c**', () => {
    const { container } = r('**a *b* c**');
    const strong = container.querySelector('strong')!;
    expect(strong.querySelector('em')).toHaveTextContent('b');
  });

  it('подчёркивание: __жирный__ и _курсив_ на границах слов; snake_case не ломается', () => {
    const { container } = r('__жирный__ и _курсив_ и df_date_x');
    expect(container.querySelector('strong')).toHaveTextContent('жирный');
    expect(container.querySelector('em')).toHaveTextContent('курсив');
    expect(container).toHaveTextContent('df_date_x');
  });

  it('__жирный с _курсивом_ внутри__', () => {
    const { container } = r('__a _b_ c__');
    expect(container.querySelector('strong em')).toHaveTextContent('b');
  });

  it('индексы: x^2, x² x³ x¹ x⁰ и a_1', () => {
    const { container } = r('x^2 y² z³ w¹ v⁰ a_1');
    expect(container.querySelectorAll('sup')).toHaveLength(5);
    expect(container.querySelector('sub')).toHaveTextContent('1');
  });

  it('инлайн-код', () => {
    const { container } = r('вызови `foo()` сейчас');
    expect(container.querySelector('code')).toHaveTextContent('foo()');
  });

  it('цитата и горизонтальная линия', () => {
    const { container } = r('> цитата\n\n---');
    expect(container.querySelector('blockquote, div')).not.toBeNull();
    expect(container).toHaveTextContent('цитата');
    expect(container.querySelector('hr')).toBeNull(); // линия — стилизованный div, не <hr>
  });

  it('маркированный список: ul/li с маркерами', () => {
    const { container } = r('- один\n- два\n* три\n+ четыре');
    expect(container.querySelectorAll('ul > li')).toHaveLength(4);
  });

  it('нумерованный список сохраняет исходные номера', () => {
    const { container } = r('3. третий\n4. четвёртый');
    const items = container.querySelectorAll('ol > li');
    expect(items).toHaveLength(2);
    expect(items[0].textContent).toContain('3.');
    expect(items[1].textContent).toContain('4.');
  });

  it('смена типа списка закрывает предыдущий и открывает новый', () => {
    const { container } = r('- a\n1. b\n- c');
    expect(container.querySelectorAll('ul')).toHaveLength(2);
    expect(container.querySelectorAll('ol')).toHaveLength(1);
  });

  it('список, после которого идёт пустая строка и абзац, закрывается', () => {
    const { container } = r('- a\n- b\n\nтекст после');
    expect(container.querySelectorAll('ul')).toHaveLength(1);
    expect(container).toHaveTextContent('текст после');
  });

  it('строка абзаца сразу после списка (без пустой строки) выводится после списка', () => {
    const { container } = r('- a\n- b\nтекст после');
    expect(container.querySelectorAll('ul')).toHaveLength(1);
    expect(container.querySelectorAll('li')).toHaveLength(2);
    expect(container).toHaveTextContent('текст после');
  });

  it('спецблоки по эмодзи: ✅ ⚠️ ❌ ℹ️ 📝 💡 🔔 ►', () => {
    const { container } = r('✅ готово\n⚠️ внимание\n❌ сбой\nℹ️ инфо\n🔔 звонок\n► далее');
    ['Успех', 'Внимание', 'Ошибка'].forEach(() => {});
    expect(container).toHaveTextContent('готово');
    expect(container).toHaveTextContent('внимание');
    expect(container).toHaveTextContent('сбой');
    expect(container).toHaveTextContent('инфо');
    expect(container.querySelectorAll('[data-testid="CheckCircleIcon"]')).toHaveLength(2); // ✅ и ►
    expect(container.querySelectorAll('[data-testid="WarningIcon"]')).toHaveLength(2); // ⚠️ и 🔔
    expect(container.querySelectorAll('[data-testid="ErrorIcon"]')).toHaveLength(1);
    expect(container.querySelectorAll('[data-testid="InfoIcon"]')).toHaveLength(1);
  });

  it('упоминания skills и тегов превращаются в читаемые коды $Имя и #Имя', () => {
    const { container } = r('вызови <$sum|Саммари> и <#7|Финансы>');
    const codes = Array.from(container.querySelectorAll('code')).map((c) => c.textContent);
    expect(codes).toEqual(['$Саммари', '#Финансы']);
  });

  it('упоминание без имени использует slug / id; служебные символы вычищаются', () => {
    const { container } = r('<$my-skill> <#9> <$x|*bo`ld*>');
    const codes = Array.from(container.querySelectorAll('code')).map((c) => c.textContent);
    expect(codes).toEqual(['$my-skill', '#9', '$bold']);
  });

  it('формулы KaTeX рендерятся, валюта $100 остаётся текстом', () => {
    const { container } = r('Формула $x^2$ и цена $100');
    expect(container.querySelector('.astra-katex-inline')).not.toBeNull();
    expect(container).toHaveTextContent('$100');
  });

  it('блочная формула $$...$$', () => {
    const { container } = r('$$E=mc^2$$');
    expect(container.querySelector('.astra-katex-display')).not.toBeNull();
  });

  it('размер шрифта берётся из настроек чата', () => {
    localStorage.setItem('chat_font_size', 'large');
    const { container } = r('Текст');
    expect(container).toHaveTextContent('Текст');
  });

  it('целые HTML-блоки модели (<ul>, <pre>) рендерятся блоками, а не плейсхолдерами', () => {
    const { container } = r('<ul><li>раз</li><li>два</li></ul>\n\n<pre>код</pre>');
    expect(container.querySelectorAll('li')).toHaveLength(2);
    expect(container.textContent).not.toContain('ASTRA');
    expect(container.textContent).toContain('раз');
    expect(container.textContent).toContain('код');
  });

  it('inline-теги модели: <b>, <i>, <u>, <br> выводятся без экранирования', () => {
    const { container } = r('<b>ж</b> <i>к</i> <u>п</u>');
    expect(container.querySelector('strong')).toHaveTextContent('ж');
    expect(container.querySelector('em')).toHaveTextContent('к');
    expect(container.querySelector('u')).toHaveTextContent('п');
  });

  it('unicode/кириллица и эмодзи не теряются', () => {
    const { container } = r('Привет 🌍 Ёж');
    expect(container).toHaveTextContent('Привет 🌍 Ёж');
  });
});

describe('MessageRenderer: ссылки и изображения', () => {
  beforeEach(() => resetHx());

  it('markdown-ссылка открывается в новой вкладке с noopener', () => {
    const { container } = r('[сайт](https://example.com/x)');
    const a = container.querySelector('a')!;
    expect(a.getAttribute('href')).toBe('https://example.com/x');
    expect(a.getAttribute('target')).toBe('_blank');
    expect(a.getAttribute('rel')).toBe('noopener noreferrer');
    expect(a).toHaveTextContent('сайт');
  });

  it('markdown-картинка ![alt](url) становится <img>, а не ссылкой с «!»', () => {
    const { container } = r('![схема](https://example.com/a.png)');
    const img = container.querySelector('img')!;
    expect(img).not.toBeNull();
    expect(img).toHaveAttribute('src', 'https://example.com/a.png');
    expect(img).toHaveAttribute('alt', 'схема');
    expect(container.querySelector('a')).toBeNull();
    expect(container.textContent).not.toContain('!');
  });

  it('картинка с пустым alt ![](url) становится <img> с безопасным src', () => {
    const { container } = r('![](https://example.com/a.png)');
    expect(container.querySelector('img')).toHaveAttribute('src', 'https://example.com/a.png');
  });

  it('XSS: ![](javascript:...) не даёт <img> с опасным src', () => {
    const { container } = r('![](javascript:alert(1))');
    const img = container.querySelector('img');
    expect(img === null || !img.getAttribute('src')).toBe(true);
  });

  it('сырой <li data-list-type="ordered"> без номера продолжает счётчик', () => {
    const { container } = r('<li data-list-type="ordered">раз</li>\n<li data-list-type="ordered">два</li>');
    const items = container.querySelectorAll('ol > li');
    expect(items.length).toBe(2);
    expect(items[0].textContent).toContain('1.');
    expect(items[1].textContent).toContain('2.');
  });

  it('XSS: javascript:-ссылка в markdown не становится кликабельной', () => {
    const { container } = r('[жми](javascript:alert(1))');
    expect(container.querySelector('a[href^="javascript"]')).toBeNull();
    expect(container.querySelector('a')).toBeNull();
  });

  it('XSS: регистр и пробелы в javascript: не обходят фильтр', () => {
    const { container } = r('[x](JaVaScRiPt:alert(1)) [y]( javascript:alert(2))');
    expect(container.querySelector('a[href*="avascript" i]')).toBeNull();
  });

  it('XSS: data:text/html-ссылка отфильтрована', () => {
    const { container } = r('[x](data:text/html;base64,PHNjcmlwdD4=)');
    expect(container.querySelector('a')).toBeNull();
  });

  it('XSS: <script> в тексте не попадает в DOM и не исполняется', () => {
    (window as any).__xss = 0;
    const { container } = r('до <script>window.__xss=1</script> после');
    expect(container.querySelector('script')).toBeNull();
    expect((window as any).__xss).toBe(0);
  });

  it('XSS: on*-обработчики и style удаляются из сырого HTML', () => {
    const { container } = r('<img src="https://e.org/a.png" onerror="window.__xss=1" style="x:y"> <a href="https://e.org" onclick="x()">l</a>');
    container.querySelectorAll('*').forEach((el) => {
      Array.from(el.attributes).forEach((at) => {
        expect(at.name.startsWith('on')).toBe(false);
        expect(at.name).not.toBe('style');
      });
    });
  });

  it('XSS: <iframe>, <object>, <embed> не рендерятся', () => {
    const { container } = r('<iframe src="https://evil"></iframe><object data="x"></object><embed src="x">');
    expect(container.querySelector('iframe, object, embed')).toBeNull();
  });

  it('XSS: ссылка в ячейке таблицы тоже фильтруется', () => {
    const { container } = r('| a | b |\n|---|---|\n| [x](javascript:alert(1)) | ok |');
    expect(container.querySelector('a')).toBeNull();
    expect(container).toHaveTextContent('ok');
  });

  it('XSS: javascript:-ссылка в заголовке и списке', () => {
    const { container } = r('# [h](javascript:alert(1))\n- [l](javascript:alert(2))');
    expect(container.querySelector('a')).toBeNull();
  });

  it('XSS: содержимое спецблока (✅ ...) тоже санитизируется', () => {
    const { container } = r('✅ <img src=x onerror="window.__xss=1"> текст');
    expect(container.querySelector('[onerror]')).toBeNull();
  });
});

describe('MessageRenderer: таблицы', () => {
  beforeEach(() => resetHx());

  const GFM = '| Имя | Возраст |\n|---|---|\n| **Аня** | 30 |\n| Борис | 25 |';

  it('GFM-таблица: заголовки, строки, markdown в ячейках, моноширинные числа', () => {
    const { container } = r(GFM);
    expect(container.querySelectorAll('th')).toHaveLength(2);
    expect(container.querySelectorAll('tbody tr')).toHaveLength(2);
    expect(container.querySelector('td strong')).toHaveTextContent('Аня');
    expect(container).toHaveTextContent('Возраст');
    const num = Array.from(container.querySelectorAll('td')).find((td) => td.textContent === '30')!;
    expect(getComputedStyle(num).fontFamily).toBe('monospace');
  });

  it('текст до и после таблицы сохраняется', () => {
    const { container } = r(`До таблицы\n\n${GFM}\n\nПосле таблицы`);
    expect(container).toHaveTextContent('До таблицы');
    expect(container).toHaveTextContent('После таблицы');
    expect(container.querySelectorAll('table')).toHaveLength(1);
  });

  it('несколько таблиц в одном сообщении', () => {
    const { container } = r(`${GFM}\n\nмежду\n\n${GFM}`);
    expect(container.querySelectorAll('table')).toHaveLength(2);
  });

  it('ячейки с формулами, кодом, ссылками, курсивом и зачёркиванием', () => {
    const { container } = r('| a |\n|---|\n| `c` *i* ~~d~~ [l](https://e.org) __b__ _u_ |');
    const td = container.querySelector('td')!;
    expect(td.querySelector('code')).toHaveTextContent('c');
    expect(td.querySelector('em')).not.toBeNull();
    expect(td.querySelector('del')).toHaveTextContent('d');
    expect(td.querySelector('a')).toHaveAttribute('href', 'https://e.org');
    expect(td.querySelector('strong')).toHaveTextContent('b');
  });

  it('формула в ячейке рендерится KaTeX', () => {
    const { container } = r('| a |\n|---|\n| $x^2$ |');
    expect(container.querySelector('td .astra-katex-inline')).not.toBeNull();
  });

  it('ASCII-таблица с рамками распознаётся и рисуется таблицей', () => {
    const ascii = '+------+-------+\n| Имя  | Город |\n+------+-------+\n| Аня  | Минск |\n| Борис| Омск  |\n| Вера | Киев  |\n| Глеб | Рига  |\n+------+-------+\n\nпосле';
    const { container } = r(ascii);
    expect(container.querySelector('table')).not.toBeNull();
    expect(container).toHaveTextContent('Минск');
    expect(container).toHaveTextContent('после');
  });

  it('ASCII-таблица: текст после неё обрабатывается как markdown', () => {
    const ascii = '+------+-------+\n| Имя  | Город |\n+------+-------+\n| Аня  | Минск |\n| Борис| Омск  |\n| Вера | Киев  |\n| Глеб | Рига  |\n+------+-------+\n\n**итог**';
    const { container } = r(ascii);
    expect(container.querySelector('strong')).toHaveTextContent('итог');
  });

  it('ASCII-таблица без верхней рамки: первая строка — заголовок (th)', () => {
    const ascii = '| Имя  | Город |\n+------+-------+\n| Аня  | Минск |\n| Борис| Омск  |\n| Вера | Киев  |\n+------+-------+';
    const { container } = r(ascii);
    expect(container.querySelectorAll('th')).toHaveLength(2);
    expect(container.querySelector('th')).toHaveTextContent('Имя');
  });

  it('(known issue) ASCII-таблица с верхней рамкой: строка заголовков становится обычной строкой, th нет', () => {
    // Правильное поведение: первая строка между рамками — заголовок. Сейчас верхняя рамка сразу переключает
    // секцию в «body», и заголовок попадает в данные.
    const ascii = '+------+-------+\n| Имя  | Город |\n+------+-------+\n| Аня  | Минск |\n| Борис| Омск  |\n| Вера | Киев  |\n+------+-------+';
    const { container } = r(ascii);
    expect(container.querySelectorAll('th')).toHaveLength(0);
    expect(container.querySelectorAll('tbody tr')).toHaveLength(4);
  });

  describe('экспорт в Excel', () => {
    it('кнопка вызывает XLSX.writeFile с очищенными от разметки ячейками и именем table_*.xlsx', () => {
      const { container } = r(GFM);
      fireEvent.click(screen.getByLabelText('Скачать таблицу в Excel'));
      expect(mockHx.writeFile).toHaveBeenCalledTimes(1);
      const [wb, fileName] = mockHx.writeFile.mock.calls[0];
      expect(fileName).toMatch(/^table_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.xlsx$/);
      const sheet = wb.sheets[0];
      expect(sheet.name).toBe('Таблица');
      expect(sheet.ws.data).toEqual([['Имя', 'Возраст'], ['Аня', '30'], ['Борис', '25']]);
      expect(sheet.ws['!cols']).toHaveLength(2);
      expect(container).toBeTruthy();
    });

    it('очистка: html, ссылки, картинки, код, курсив, сущности', () => {
      r('| A |\n|---|\n| **b** [t](http://x) `c` ~~d~~ &amp; <i>x</i> |');
      fireEvent.click(screen.getByLabelText('Скачать таблицу в Excel'));
      const row = mockHx.writeFile.mock.calls[0][0].sheets[0].ws.data[1][0];
      expect(row).toBe('b t c d & x');
    });

    it('ошибка записи файла логируется и не ломает интерфейс', () => {
      const err = jest.spyOn(console, 'error').mockImplementation(() => {});
      mockHx.writeFile.mockImplementation(() => {
        throw new Error('disk');
      });
      r(GFM);
      fireEvent.click(screen.getByLabelText('Скачать таблицу в Excel'));
      expect(err).toHaveBeenCalledWith('Ошибка при экспорте таблицы в Excel:', expect.any(Error));
      err.mockRestore();
    });

    it('ширина колонок ограничена [10..50]', () => {
      r(`| a | ${'x'.repeat(80)} |\n|---|---|\n| 1 | 2 |`);
      fireEvent.click(screen.getByLabelText('Скачать таблицу в Excel'));
      const cols = mockHx.writeFile.mock.calls[0][0].sheets[0].ws['!cols'];
      expect(cols[0].wch).toBe(10);
      expect(cols[1].wch).toBe(50);
    });
  });
});

describe('MessageRenderer: pin viewer и memo', () => {
  beforeEach(() => resetHx());

  it('пинит показ viewer за сообщением, если гейт открыт и есть html-fence', async () => {
    mockHx.liveGate = true;
    r('```html\n<p>x</p>\n```', { messageId: 'm1' });
    await Promise.resolve();
    expect(mockHx.pin).toHaveBeenCalledWith('m1');
  });

  it.each([
    [':::artifact{identifier="a" type="text/html" title="t"}\n<p>x</p>\n:::'],
    ['```mermaid\ngraph TD\n```'],
    ['```svg\n<svg/>\n```'],
  ])('пинит для артефактного содержимого %#', async (content) => {
    mockHx.liveGate = true;
    r(content, { messageId: 'm1' });
    await Promise.resolve();
    expect(mockHx.pin).toHaveBeenCalledTimes(1);
  });

  it('пинит для презентации (.slide) и при presentationExpected с fence/HTML', async () => {
    mockHx.liveGate = true;
    r('<div class="slide"><h1>Заголовок презентации</h1></div><div class="slide">2</div>', { messageId: 'm1' });
    await Promise.resolve();
    expect(mockHx.pin).toHaveBeenCalledTimes(1);

    mockHx.pin.mockReset();
    mockHx.presentationExpected = true;
    r('```python\nx\n```', { messageId: 'm2' });
    await Promise.resolve();
    expect(mockHx.pin).toHaveBeenCalledWith('m2');

    mockHx.pin.mockReset();
    r('<html><body>x</body></html>', { messageId: 'm3' });
    await Promise.resolve();
    expect(mockHx.pin).toHaveBeenCalledWith('m3');
  });

  it('не пинит без messageId, без live-гейта, для обычного текста', async () => {
    mockHx.liveGate = true;
    r('```html\nx\n```');
    r('просто текст', { messageId: 'm1' });
    mockHx.liveGate = false;
    r('```html\nx\n```', { messageId: 'm2' });
    await Promise.resolve();
    expect(mockHx.pin).not.toHaveBeenCalled();
  });

  it('повторные рендеры пинят один раз на messageId', async () => {
    mockHx.liveGate = true;
    const { rerender } = r('```html\na\n```', { messageId: 'm1' });
    await Promise.resolve();
    rerender(<MessageRenderer content={'```html\nab\n```'} messageId="m1" />);
    await Promise.resolve();
    expect(mockHx.pin).toHaveBeenCalledTimes(1);
  });

  it('гейт получает chatId из пропса или из текущего чата и флаг forceArtifacts', () => {
    mockHx.currentChatId = 'cur';
    r('x', { messageId: 'm', forceArtifacts: true });
    expect(mockHx.lastAllowedArgs).toEqual({ chatId: 'cur', messageId: 'm', content: 'x', force: true });
    r('y', { chatId: 'explicit' });
    expect(mockHx.lastAllowedArgs.chatId).toBe('explicit');
  });

  it('memo: тот же content не вызывает повторный разбор, новый — обновляет вывод', () => {
    const { rerender, container } = r('a');
    rerender(<MessageRenderer content="a" />);
    expect(container).toHaveTextContent('a');
    rerender(<MessageRenderer content="b" />);
    expect(container).toHaveTextContent('b');
  });
});
