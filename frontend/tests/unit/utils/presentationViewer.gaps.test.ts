import {
  coalescePresentationHtmlInMessage,
  extractDeckSlideFragments,
  extractUnfencedPresentationHtml,
  getStablePresentationSnapshot,
  openPresentationViewer,
  presentationLog,
  sanitizePresentationHtmlSource,
  shouldOpenPresentationViewer,
  shouldTreatHtmlFenceAsPresentationStream,
} from '@/utils/presentationViewer';

const slide = (n: number) => `<div class="slide"><h1>S${n}</h1></div>`;
const FULL_HEAD = '<!DOCTYPE html><html><head><style>.slide{width:1px}</style></head><body>';

describe('presentationViewer: отладочный лог', () => {
  afterEach(() => {
    jest.restoreAllMocks();
    localStorage.clear();
  });

  it('пишет в console.info только при включённом флаге', () => {
    const info = jest.spyOn(console, 'info').mockImplementation(() => {});
    presentationLog('событие');
    expect(info).not.toHaveBeenCalled();
    localStorage.setItem('astra:debug:presentation', '1');
    presentationLog('событие', { a: 1 });
    presentationLog('без деталей');
    expect(info).toHaveBeenNthCalledWith(1, '[presentation] событие', { a: 1 });
    expect(info).toHaveBeenNthCalledWith(2, '[presentation] без деталей', {});
  });

  it('недоступный localStorage отключает лог без исключения', () => {
    const info = jest.spyOn(console, 'info').mockImplementation(() => {});
    jest.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('denied');
    });
    expect(() => presentationLog('x')).not.toThrow();
    expect(info).not.toHaveBeenCalled();
  });

  it('ошибка console.info поглощается', () => {
    localStorage.setItem('astra:debug:presentation', '1');
    jest.spyOn(console, 'info').mockImplementation(() => {
      throw new Error('console broken');
    });
    expect(() => presentationLog('x')).not.toThrow();
  });
});

describe('presentationViewer: определение стрима презентации', () => {
  it('shouldTreatHtmlFenceAsPresentationStream: короткий признак --deck- распознаётся по подсказкам', () => {
    const opts = { isStreaming: true, presentationExpected: true };
    expect(shouldTreatHtmlFenceAsPresentationStream('--deck-', '```css', '', opts)).toBe(true);
    expect(shouldTreatHtmlFenceAsPresentationStream('просто', '```', '', opts)).toBe(false);
    expect(shouldTreatHtmlFenceAsPresentationStream('--deck-', '```css', '', { isStreaming: true })).toBe(false);
    expect(shouldTreatHtmlFenceAsPresentationStream('x', '```', 'python', opts)).toBe(false);
    expect(shouldTreatHtmlFenceAsPresentationStream('x', '```', 'html', opts)).toBe(true);
  });

  it('shouldOpenPresentationViewer: html-fence при стриме skill-презентации открывает viewer сразу', () => {
    expect(
      shouldOpenPresentationViewer('<p>x</p>', { isStreaming: true, presentationExpected: true, language: 'HTML' }),
    ).toBe(true);
    expect(shouldOpenPresentationViewer('<p>x</p>', { isStreaming: false, presentationExpected: true, language: 'html' })).toBe(
      false,
    );
  });

  it('shouldOpenPresentationViewer: потоковые признаки и ранний chrome документа', () => {
    const o = { isStreaming: true, presentationExpected: true, language: 'text' };
    expect(shouldOpenPresentationViewer('<!DOCTYPE html>', o)).toBe(true);
    expect(shouldOpenPresentationViewer('<style>.a{}</style>', o)).toBe(true);
    expect(shouldOpenPresentationViewer('@font-face{}', o)).toBe(true);
    expect(shouldOpenPresentationViewer('<div class="content-zone">x</div>', o)).toBe(true);
    expect(shouldOpenPresentationViewer('обычный текст', o)).toBe(false);
  });
});

describe('presentationViewer: извлечение HTML и слайдов', () => {
  it('extractUnfencedPresentationHtml: слайд без кавычек в class — весь текст считается презентацией', () => {
    const text = '<div class=slide><h1>Привет мир, это достаточно длинный заголовок</h1></div>';
    expect(extractUnfencedPresentationHtml(text)).toEqual({ before: '', html: text, after: '' });
    const shortText = '<p class=slide>x</p>';
    expect(extractUnfencedPresentationHtml(shortText).html).toBe(shortText);
  });

  it('extractUnfencedPresentationHtml: текст с тегами без признаков презентации не извлекается', () => {
    const text = 'абзац <b>жирный</b> без слайдов';
    expect(extractUnfencedPresentationHtml(text)).toEqual({ before: text, html: null, after: '' });
    expect(extractUnfencedPresentationHtml('')).toEqual({ before: '', html: null, after: '' });
  });

  it('extractUnfencedPresentationHtml: ранний chrome при стриме скилла', () => {
    const r = extractUnfencedPresentationHtml('вступление\n<style>.x{}</style>', {
      presentationExpected: true,
      isStreaming: true,
    });
    expect(r.before).toBe('вступление\n');
    expect(r.html).toBe('<style>.x{}</style>');
  });

  it('extractDeckSlideFragments: class=slide без кавычек и объединение соседних совпадений', () => {
    const frags = extractDeckSlideFragments('<div class=slide>a</div><div class=slide>b</div>');
    expect(frags).toEqual(['<div class=slide>a</div>', '<div class=slide>b</div>']);
    expect(extractDeckSlideFragments('')).toEqual([]);
  });
});

describe('presentationViewer: sanitizePresentationHtmlSource', () => {
  it('убирает ```html-обёртку и обрезает недописанный комментарий', () => {
    const out = sanitizePresentationHtmlSource('```html\n<div class="slide">a</div>\n<!-- нез\n```');
    expect(out).toBe('<div class="slide">a</div>');
  });

  it('пустой ввод и BOM', () => {
    expect(sanitizePresentationHtmlSource('')).toBe('');
    expect(sanitizePresentationHtmlSource('﻿  <p>x</p> ')).toBe('<p>x</p>');
  });

  it('fence без языка: открывающая обёртка остаётся, закрывающая срезается (known issue)', () => {
    // Правильно: безымянный fence ``` тоже снимается с обеих сторон.
    // Сейчас regex требует границу слова после ```, поэтому открывающие ``` остаются.
    const out = sanitizePresentationHtmlSource('```\n<div class="slide">a</div>\n```');
    expect(out).toBe('```\n<div class="slide">a</div>');
  });
});

describe('presentationViewer: coalescePresentationHtmlInMessage', () => {
  it('без признаков презентации возвращает текст как есть', () => {
    expect(coalescePresentationHtmlInMessage('обычный ответ')).toBe('обычный ответ');
    expect(coalescePresentationHtmlInMessage('')).toBe('');
  });

  it('html-fence без слайдов не трогается', () => {
    const t = '```html\n<p>без слайдов</p>\n```';
    expect(coalescePresentationHtmlInMessage(t)).toBe(t);
  });

  it('склеивает несколько блоков со слайдами в один документ, убирает дубликаты и добавляет fallback CSS', () => {
    const text = `Вот презентация\n\n\`\`\`html\n${slide(1)}\n${slide(2)}\n\`\`\`\n\`\`\`html\n${slide(2)}\n${slide(3)}</body></html>\n\`\`\``;
    const out = coalescePresentationHtmlInMessage(text);
    expect(out.startsWith('Вот презентация\n\n```html\n<!DOCTYPE html>')).toBe(true);
    expect(out.endsWith('</html>\n```')).toBe(true);
    expect((out.match(/class="slide"/g) || []).length).toBe(3);
    expect(out).toContain('Cera CY'); // подставлен запасной chrome
    expect(out).toContain('<h1>S1</h1>');
    expect(out).toContain('<h1>S3</h1>');
  });

  it('сохраняет chrome из исходного документа', () => {
    const text = `${FULL_HEAD}${slide(1)}${slide(2)}</body></html>`;
    const out = coalescePresentationHtmlInMessage(text);
    expect(out).toContain('.slide{width:1px}');
    expect(out).not.toContain('Cera CY'); // свой style уже есть, запасной не нужен
  });

  it('chrome без style: добавляется link и запасной style', () => {
    const text = `<head><link rel="stylesheet" href="/a.css"></head>${slide(1)}`;
    const out = coalescePresentationHtmlInMessage(text);
    expect(out).toContain('<link rel="stylesheet" href="/a.css">');
    expect(out).toContain('<style>');
  });
});

describe('presentationViewer: coalesce, хвосты и class без кавычек', () => {
  it('обрезает недописанный html-комментарий в конце слайда', () => {
    const out = coalescePresentationHtmlInMessage(`${slide(1)}<div class="slide"><h1>S2</h1><!-- cut`);
    expect(out).toContain('<h1>S2</h1>');
    expect(out).not.toContain('cut');
  });

  it('слайды с class=slide без кавычек склеиваются, если есть DOCTYPE', () => {
    const out = coalescePresentationHtmlInMessage(
      '<!DOCTYPE html><html><head></head><body><div class=slide>A</div><div class=slide>B</div></body></html>',
    );
    expect(out).toContain('<div class=slide>A</div>');
    expect(out).toContain('<div class=slide>B</div>');
    expect(out.startsWith('```html\n<!DOCTYPE html>')).toBe(true);
  });

  it('class=slide без кавычек и без DOCTYPE не распознаётся как начало презентации (known issue)', () => {
    // Правильно: такие же слайды, как и с кавычками, должны склеиваться в один ```html-документ.
    // Сейчас поиск начала (firstSlide) требует кавычек вокруг значения class.
    const text = '<div class=slide>A</div><div class=slide>B</div>';
    expect(coalescePresentationHtmlInMessage(text)).toBe(text);
  });
});

describe('presentationViewer: getStablePresentationSnapshot (завершённое сообщение)', () => {
  const RealParser = global.DOMParser;
  afterEach(() => {
    (global as any).DOMParser = RealParser;
    jest.restoreAllMocks();
    localStorage.clear();
  });

  const fakeParser = (count: number | (() => never)) => {
    (global as any).DOMParser = class {
      parseFromString() {
        if (typeof count === 'function') count();
        return { querySelectorAll: () => new Array(count as number) };
      }
    };
  };

  it('нормальный документ: все слайды готовы, pending=false', () => {
    const r = getStablePresentationSnapshot(`${FULL_HEAD}${slide(1)}${slide(2)}</body></html>`, false);
    expect(r.readyCount).toBe(2);
    expect(r.startedCount).toBe(2);
    expect(r.pending).toBe(false);
    expect(r.html).toContain('<h1>S2</h1>');
  });

  it('пустой <head> без style: документ собирается из фрагментов с запасным CSS', () => {
    const r = getStablePresentationSnapshot(`<html><head></head><body>${slide(1)}</body></html>`, false);
    expect(r.html).toContain('Cera CY');
    expect(r.readyCount).toBe(1);
  });

  it('нет слайдов: html=null и нулевые счётчики', () => {
    const r = getStablePresentationSnapshot('<p>просто текст</p>', false);
    expect(r).toEqual({ html: null, readyCount: 0, startedCount: 0, pending: false });
  });

  it('пустой ввод: pending повторяет isStreaming', () => {
    expect(getStablePresentationSnapshot('', false).pending).toBe(false);
    expect(getStablePresentationSnapshot('   ', true)).toEqual({ html: null, readyCount: 0, startedCount: 0, pending: true });
  });

  it('DOMParser «съел» слайд: документ пересобирается из regex-фрагментов', () => {
    fakeParser(1);
    const r = getStablePresentationSnapshot(`${FULL_HEAD}${slide(1)}${slide(2)}</body></html>`, false);
    expect(r.readyCount).toBe(2);
    expect(r.startedCount).toBe(2);
    expect(r.html).toContain('<h1>S2</h1>');
    expect(r.pending).toBe(false);
  });

  it('DOMParser видит 0 слайдов, а regex 1: пересборка из фрагментов', () => {
    fakeParser(0);
    const r = getStablePresentationSnapshot(`${FULL_HEAD}${slide(1)}</body></html>`, false);
    expect(r.readyCount).toBe(1);
    expect(r.html).toContain('<h1>S1</h1>');
  });

  it('DOMParser видит больше слайдов, чем regex-фрагментов: берётся максимум', () => {
    fakeParser(5);
    const html = `${FULL_HEAD}${slide(1)}</body></html>`;
    const r = getStablePresentationSnapshot(html, false);
    expect(r.readyCount).toBe(5);
    expect(r.startedCount).toBe(5);
    expect(r.html).toContain('<h1>S1</h1>');
  });

  it('ошибка DOMParser: фолбэк на regex-фрагменты', () => {
    fakeParser(() => {
      throw new Error('parser down');
    });
    const r = getStablePresentationSnapshot(`${FULL_HEAD}${slide(1)}${slide(2)}</body></html>`, false);
    expect(r.readyCount).toBe(2);
    expect(r.html).toContain('<h1>S1</h1>');
    expect(r.html).toContain('.slide{width:1px}');
  });

  it('ошибка DOMParser без слайдов: html=null', () => {
    fakeParser(() => {
      throw new Error('parser down');
    });
    const r = getStablePresentationSnapshot('<p>нет слайдов</p>', false);
    expect(r).toEqual({ html: null, readyCount: 0, startedCount: 0, pending: false });
  });

  it('включённый лог описывает расхождение DOM и regex и обрезанный хвост', () => {
    localStorage.setItem('astra:debug:presentation', '1');
    const info = jest.spyOn(console, 'info').mockImplementation(() => {});
    getStablePresentationSnapshot(`${FULL_HEAD}${slide(1)}<!-- не закрыт`, false);
    fakeParser(1);
    getStablePresentationSnapshot(`${FULL_HEAD}${slide(1)}${slide(2)}`, false);
    (global as any).DOMParser = RealParser;
    getStablePresentationSnapshot('<p>нет</p>', false);
    const msgs = info.mock.calls.map((c) => String(c[0]));
    expect(msgs.some((m) => m.includes('расхождение DOM vs regex'))).toBe(true);
    expect(msgs.some((m) => m.includes('НЕТ слайдов'))).toBe(true);
  });
});

describe('presentationViewer: getStablePresentationSnapshot (стрим)', () => {
  it('последний слайд не показывается, пока он дописывается', () => {
    const r = getStablePresentationSnapshot(`${FULL_HEAD}${slide(1)}${slide(2)}<div class="slide"><h1>S3`, true);
    expect(r.pending).toBe(true);
    expect(r.startedCount).toBe(3);
    expect(r.readyCount).toBe(2);
    expect(r.html).toContain('<h1>S2</h1>');
    expect(r.html).not.toContain('S3');
  });

  it('maxReadyCount ограничивает число готовых слайдов; 0 даёт пустой результат', () => {
    const code = `${FULL_HEAD}${slide(1)}${slide(2)}${slide(3)}`;
    const one = getStablePresentationSnapshot(code, true, { maxReadyCount: 1 });
    expect(one.readyCount).toBe(1);
    expect(one.html).not.toContain('S2');
    const zero = getStablePresentationSnapshot(code, true, { maxReadyCount: 0 });
    expect(zero).toMatchObject({ html: null, readyCount: 0, pending: true });
  });

  it('один начатый слайд ещё не готов', () => {
    const r = getStablePresentationSnapshot('<div class="slide"><h1>S1', true);
    expect(r).toMatchObject({ html: null, readyCount: 0, startedCount: 1, pending: true });
  });
});

describe('presentationViewer: openPresentationViewer', () => {
  afterEach(() => {
    jest.restoreAllMocks();
    localStorage.clear();
  });

  it('кладёт очищенный HTML в localStorage и открывает вкладку', () => {
    const open = jest.spyOn(window, 'open').mockReturnValue({} as Window);
    openPresentationViewer('```html\n<div class="slide">a</div>\n```');
    const keys = Object.keys(localStorage);
    expect(keys).toHaveLength(1);
    expect(localStorage.getItem(keys[0])).toBe('<div class="slide">a</div>');
    expect(String(open.mock.calls[0][0])).toMatch(/presentation-viewer\.html\?key=/);
    expect(open.mock.calls[0][1]).toBe('_blank');
  });

  it('если окно заблокировано, ключ удаляется и бросается ошибка', () => {
    jest.spyOn(window, 'open').mockReturnValue(null);
    expect(() => openPresentationViewer('<p>x</p>')).toThrow('Всплывающее окно заблокировано');
    expect(Object.keys(localStorage)).toHaveLength(0);
  });

  it('переполнение хранилища даёт понятную ошибку и окно не открывается', () => {
    const open = jest.spyOn(window, 'open');
    jest.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('quota');
    });
    expect(() => openPresentationViewer('<p>x</p>')).toThrow('Не удалось сохранить HTML презентации');
    expect(open).not.toHaveBeenCalled();
  });
});
