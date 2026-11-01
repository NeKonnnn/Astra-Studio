import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom';
import InlinePresentationViewer, { buildInlinePresentationViewerSrcDoc } from '@/components/InlinePresentationViewer';

const mockSnapshot = jest.fn();
const mockOpenPresentationViewer = jest.fn();
const mockPresentationLog = jest.fn();
let mockInView = true;

jest.mock('@/utils/presentationViewer', () => ({
  getStablePresentationSnapshot: (...a: unknown[]) => mockSnapshot(...a),
  openPresentationViewer: (...a: unknown[]) => mockOpenPresentationViewer(...a),
  presentationLog: (...a: unknown[]) => mockPresentationLog(...a),
}));
jest.mock('@/hooks/useInViewport', () => ({
  useInViewport: () => ({ ref: { current: null }, inView: mockInView }),
}));

// Управляемый стаб снимка: полный HTML отдаёт ready/started, усечённый (maxReadyCount) — snapN.
let fullReady = 3;
let fullStarted = 4;
let fullHtml: string | null = 'full-html';

function installSnapshotStub() {
  mockSnapshot.mockImplementation((_html: string, _streaming: boolean, opts?: { maxReadyCount?: number }) => {
    if (opts && typeof opts.maxReadyCount === 'number') {
      return { html: `snap${opts.maxReadyCount}`, readyCount: opts.maxReadyCount, startedCount: fullStarted };
    }
    return { html: fullHtml, readyCount: fullReady, startedCount: fullStarted };
  });
}

function frame(): HTMLIFrameElement | null {
  return document.querySelector('iframe');
}

function ack(count: unknown, source?: Window | null) {
  const win = source === undefined ? frame()!.contentWindow : source;
  act(() => {
    window.dispatchEvent(new MessageEvent('message', { data: { type: 'astra-pptx-applied', count }, source: win as any }));
  });
}

describe('buildInlinePresentationViewerSrcDoc', () => {
  it('формирует самостоятельный документ с вьюером, кнопками и скриптом dom-to-pptx', () => {
    const doc = buildInlinePresentationViewerSrcDoc('');
    expect(doc).toContain('<!DOCTYPE html>');
    expect(doc).toContain('id="viewer"');
    expect(doc).toContain('id="prevBtn"');
    expect(doc).toContain('id="exportBtn"');
    expect(doc).toContain('/static/dom-to-pptx.bundle.js');
    expect(doc).toContain('width: 297mm');
    expect(doc).toContain('height: 167mm');
    expect(doc).toContain("astra-pptx-set-html");
  });

  it('PUBLIC_URL подставляется в путь скрипта без хвостового слэша', () => {
    const prev = process.env.PUBLIC_URL;
    process.env.PUBLIC_URL = '/app/';
    try {
      expect(buildInlinePresentationViewerSrcDoc('')).toContain('src="/app/static/dom-to-pptx.bundle.js"');
    } finally {
      process.env.PUBLIC_URL = prev;
    }
  });

  it('вырезает <script>…</script> из HTML презентации', () => {
    const doc = buildInlinePresentationViewerSrcDoc('<div class="slide">a</div><script>window.__x=1</script><SCRIPT src="evil.js"></SCRIPT>');
    expect(doc).toContain('<script id="src" type="text/plain"><div class="slide">a</div></script>');
    expect(doc).not.toContain('window.__x=1');
    expect(doc).not.toContain("evil.js");
  });

  it('закрывающий </script внутри данных экранируется, чтобы не завершить служебный тег раньше времени', () => {
    const doc = buildInlinePresentationViewerSrcDoc('<div class="slide">x &lt;/script&gt; </script >y</div>');
    const dataBlock = doc.match(/<script id="src" type="text\/plain">([\s\S]*?)<\/script>/)![1];
    expect(dataBlock).not.toMatch(/<\/script/i);
  });

  it('обработчики событий (onerror/onload) и javascript: в HTML презентации вырезаются', () => {
    const payload =
      '<div class="slide"><img src=x onerror="parent.parent.localStorage.clear()">' +
      '<svg onload=alert(1)></svg><a href="javascript:alert(1)">z</a>' +
      '<a href=" JaVa\tScRiPt:alert(2)">y</a></div>';
    const doc = buildInlinePresentationViewerSrcDoc(payload);
    const dataBlock = doc.match(/<script id="src" type="text\/plain">([\s\S]*?)<\/script>/)![1];
    expect(dataBlock).not.toMatch(/onerror/i);
    expect(dataBlock).not.toMatch(/onload/i);
    expect(dataBlock).not.toMatch(/javascript\s*:/i);
    expect(dataBlock).toContain('class="slide"');
    expect(dataBlock).toContain('<img src=x');
    expect(dataBlock).toContain('href="#');
  });

  it('вложенные обходы фильтра (ononerror=, разделитель /) тоже вычищаются', () => {
    const payload = '<img src=x ononerrorerror="alert(1)"><img/src=x/onerror=alert(2)>';
    const doc = buildInlinePresentationViewerSrcDoc(payload);
    const dataBlock = doc.match(/<script id="src" type="text\/plain">([\s\S]*?)<\/script>/)![1];
    expect(dataBlock).not.toMatch(/\son[a-z]+\s*=/i);
    expect(dataBlock).not.toMatch(/\/on[a-z]+\s*=/i);
  });

  it('shell-скрипт внутри iframe тоже санитизирует HTML, пришедший по postMessage', () => {
    const doc = buildInlinePresentationViewerSrcDoc('');
    expect(doc).toContain('function stripUnsafeMarkup');
    expect(doc).toContain('EVENT_HANDLER_RE_SRC');
    expect(doc).toContain('JS_URL_RE_SRC');
  });

});

describe('InlinePresentationViewer', () => {
  beforeEach(() => {
    jest.useFakeTimers('modern');
    jest.setSystemTime(new Date('2024-01-01T00:00:00Z'));
    mockSnapshot.mockReset();
    mockOpenPresentationViewer.mockReset();
    mockPresentationLog.mockReset();
    mockInView = true;
    fullReady = 3;
    fullStarted = 4;
    fullHtml = 'full-html';
    installSnapshotStub();
  });
  afterEach(() => {
    jest.useRealTimers();
    jest.restoreAllMocks();
  });

  describe('завершённая презентация', () => {
    it('готовый HTML: iframe-вьюер, статус «Презентация», кнопка открытия без спиннера', () => {
      render(<InlinePresentationViewer html="<div class='slide'>1</div>" />);
      expect(frame()).not.toBeNull();
      expect(frame()).toHaveAttribute('title', 'Просмотр презентации');
      expect(screen.getByText('Презентация')).toBeInTheDocument();
      expect(screen.queryByRole('progressbar')).toBeNull();
      expect(screen.getByLabelText('Открыть в новой вкладке')).toBeInTheDocument();
      expect(mockSnapshot).toHaveBeenCalledWith("<div class='slide'>1</div>", false);
    });

    it('shell-документ iframe содержит вьюер 297x167 мм', () => {
      render(<InlinePresentationViewer html="x" />);
      const srcdoc = frame()!.getAttribute('srcdoc')!;
      expect(srcdoc).toContain('id="viewer"');
      expect(srcdoc).toContain('<script id="src" type="text/plain"></script>');
    });

    it('sandbox iframe презентации: allow-scripts + allow-same-origin (нужен для contentDocument и шрифтов)', () => {
      render(<InlinePresentationViewer html="x" />);
      const tokens = (frame()!.getAttribute('sandbox') || '').split(/\s+/);
      expect(tokens).toEqual(
        expect.arrayContaining(['allow-scripts', 'allow-same-origin', 'allow-downloads']),
      );
    });

    it('вне экрана (не стрим) iframe не монтируется', () => {
      mockInView = false;
      render(<InlinePresentationViewer html="x" />);
      expect(frame()).toBeNull();
      expect(screen.getByText('Презентация')).toBeInTheDocument();
    });

    it('пустой снимок и не стрим -> «нет слайдов», сообщение об ошибке и лог', () => {
      fullHtml = null;
      render(<InlinePresentationViewer html="мусор" />);
      expect(screen.getByText('Презентация · нет слайдов')).toBeInTheDocument();
      expect(screen.getByText('Не удалось собрать слайды презентации')).toBeInTheDocument();
      expect(frame()).toBeNull();
      expect(mockPresentationLog).toHaveBeenCalledWith(
        'viewer: показываю «нет слайдов» (committedHtml пуст)',
        expect.objectContaining({ isStreaming: false, htmlLen: 5 }),
      );
    });

    it('html=undefined безопасен для лога длины', () => {
      fullHtml = null;
      render(<InlinePresentationViewer html={undefined as unknown as string} />);
      expect(mockPresentationLog.mock.calls[0][1].htmlLen).toBe(0);
    });

    it('открытие во внешней вкладке передаёт исходный html в openPresentationViewer', () => {
      render(<InlinePresentationViewer html="<div>исходник</div>" />);
      fireEvent.click(screen.getByLabelText('Открыть в новой вкладке'));
      expect(mockOpenPresentationViewer).toHaveBeenCalledWith('<div>исходник</div>');
    });

    it('сбой при открытии логируется и не пробрасывается', () => {
      const err = jest.spyOn(console, 'error').mockImplementation(() => {});
      mockOpenPresentationViewer.mockImplementation(() => {
        throw new Error('popup');
      });
      render(<InlinePresentationViewer html="x" />);
      fireEvent.click(screen.getByLabelText('Открыть в новой вкладке'));
      expect(err).toHaveBeenCalledWith('Failed to open presentation viewer:', expect.any(Error));
    });

    it('sourceSlot: кнопка «Показать HTML» раскрывает и скрывает исходник', () => {
      render(<InlinePresentationViewer html="x" sourceSlot={<div>ИСХОДНИК</div>} />);
      expect(screen.queryByText('ИСХОДНИК')).toBeNull();
      fireEvent.click(screen.getByLabelText('Показать HTML'));
      expect(screen.getByText('ИСХОДНИК')).toBeInTheDocument();
      fireEvent.click(screen.getByLabelText('Скрыть HTML'));
      expect(screen.getByLabelText('Показать HTML')).toBeInTheDocument();
    });

    it('без sourceSlot кнопки исходника нет', () => {
      render(<InlinePresentationViewer html="x" />);
      expect(screen.queryByLabelText('Показать HTML')).toBeNull();
    });

    it('после загрузки iframe html отправляется в него postMessage-ом', () => {
      render(<InlinePresentationViewer html="x" />);
      const f = frame()!;
      const post = jest.spyOn(f.contentWindow!, 'postMessage');
      fireEvent.load(f);
      expect(post).toHaveBeenCalledWith({ type: 'astra-pptx-set-html', html: 'full-html' }, '*');
    });

    it('смена html после загрузки iframe досылает новую версию', () => {
      const { rerender } = render(<InlinePresentationViewer html="a" />);
      const f = frame()!;
      fireEvent.load(f);
      const post = jest.spyOn(f.contentWindow!, 'postMessage');
      fullHtml = 'new-full';
      rerender(<InlinePresentationViewer html="b" />);
      expect(post).toHaveBeenCalledWith({ type: 'astra-pptx-set-html', html: 'new-full' }, '*');
    });

    it('до загрузки iframe сообщения не отправляются', () => {
      const { rerender } = render(<InlinePresentationViewer html="a" />);
      const post = jest.spyOn(frame()!.contentWindow!, 'postMessage');
      fullHtml = 'zzz';
      rerender(<InlinePresentationViewer html="b" />);
      expect(post).not.toHaveBeenCalled();
    });

    it('после ухода из viewport флаг готовности iframe сбрасывается: повторный показ ждёт onLoad', () => {
      const { rerender } = render(<InlinePresentationViewer html="a" />);
      fireEvent.load(frame()!);
      mockInView = false;
      rerender(<InlinePresentationViewer html="a" />);
      expect(frame()).toBeNull();
      mockInView = true;
      rerender(<InlinePresentationViewer html="a" />);
      const post = jest.spyOn(frame()!.contentWindow!, 'postMessage');
      fullHtml = 'second';
      rerender(<InlinePresentationViewer html="a2" />);
      expect(post).not.toHaveBeenCalled();
    });

    it('при пустом результате снимка на втором проходе прежний committedHtml сохраняется', () => {
      const { rerender } = render(<InlinePresentationViewer html="a" />);
      fullHtml = null;
      rerender(<InlinePresentationViewer html="b" />);
      expect(frame()).not.toBeNull();
      expect(screen.queryByText('Презентация · нет слайдов')).toBeNull();
    });
  });

  describe('сообщения от iframe', () => {
    it('applied: «готово N» отображается во время стрима по подтверждению от iframe', () => {
      render(<InlinePresentationViewer html="x" isStreaming />);
      expect(screen.getAllByText(/Презентация · слайд 4…|Презентация · готово/).length).toBeGreaterThan(0);
      ack(1);
      expect(screen.getAllByText('Презентация · готово 1 · слайд 2…').length).toBeGreaterThan(0);
      expect(mockPresentationLog).toHaveBeenCalledWith('iframe отрисовал слайды', { count: 1, isStreaming: true });
    });

    it('когда начатых слайдов не больше готовых, суффикс — «…»', () => {
      fullStarted = 1;
      render(<InlinePresentationViewer html="x" isStreaming />);
      ack(1);
      expect(screen.getAllByText('Презентация · готово 1…').length).toBeGreaterThan(0);
    });

    it('счётчик во время стрима не уменьшается от более старого подтверждения', () => {
      render(<InlinePresentationViewer html="x" isStreaming />);
      ack(3);
      ack(2);
      expect(screen.getAllByText(/готово 3/).length).toBeGreaterThan(0);
    });

    it('после окончания стрима подтверждение выставляет счётчик как есть (даже меньший)', () => {
      const { rerender } = render(<InlinePresentationViewer html="x" isStreaming />);
      ack(3);
      rerender(<InlinePresentationViewer html="x" isStreaming={false} />);
      ack(2);
      expect(screen.getByText('Презентация')).toBeInTheDocument();
    });

    it('count нечислового типа трактуется как 0', () => {
      render(<InlinePresentationViewer html="x" isStreaming />);
      ack('много');
      expect(mockPresentationLog).toHaveBeenCalledWith('iframe отрисовал слайды', { count: 0, isStreaming: true });
    });

    it('no-slides пишет лог и не меняет счётчик', () => {
      render(<InlinePresentationViewer html="x" isStreaming />);
      act(() => {
        window.dispatchEvent(
          new MessageEvent('message', {
            data: { type: 'astra-pptx-no-slides', htmlLen: 77 },
            source: frame()!.contentWindow as any,
          }),
        );
      });
      expect(mockPresentationLog).toHaveBeenCalledWith('iframe сообщил: .slide НЕ найдены', { htmlLen: 77, isStreaming: true });
    });

    it('сообщения с чужого источника, пустые и неизвестного типа игнорируются', () => {
      render(<InlinePresentationViewer html="x" isStreaming />);
      mockPresentationLog.mockClear();
      ack(5, window); // не окно iframe
      act(() => {
        window.dispatchEvent(new MessageEvent('message', { data: null, source: frame()!.contentWindow as any }));
        window.dispatchEvent(
          new MessageEvent('message', { data: { type: 'other' }, source: frame()!.contentWindow as any }),
        );
      });
      expect(mockPresentationLog).not.toHaveBeenCalledWith('iframe отрисовал слайды', expect.anything());
    });

    it('слушатель message снимается при размонтировании', () => {
      const { unmount } = render(<InlinePresentationViewer html="x" isStreaming />);
      const win = frame()!.contentWindow;
      unmount();
      mockPresentationLog.mockClear();
      act(() => {
        window.dispatchEvent(new MessageEvent('message', { data: { type: 'astra-pptx-applied', count: 1 }, source: win as any }));
      });
      expect(mockPresentationLog).not.toHaveBeenCalled();
    });
  });

  describe('стриминг', () => {
    it('пока нет готовых слайдов и начатых: «генерация…», спиннер, без кнопок', () => {
      fullReady = 0;
      fullStarted = 0;
      render(<InlinePresentationViewer html="" isStreaming />);
      expect(screen.getAllByText('Презентация · генерация…').length).toBeGreaterThan(0);
      expect(screen.getByRole('progressbar')).toBeInTheDocument();
      expect(screen.queryByLabelText('Открыть в новой вкладке')).toBeNull();
      expect(frame()).toBeNull(); // нечего показывать: committedHtml пуст
      expect(screen.queryByText('Не удалось собрать слайды презентации')).toBeNull();
    });

    it('пока нет готовых, но слайд начат: «слайд N…»', () => {
      fullReady = 0;
      fullStarted = 2;
      render(<InlinePresentationViewer html="" isStreaming />);
      expect(screen.getAllByText('Презентация · слайд 2…').length).toBeGreaterThan(0);
    });

    it('первый готовый слайд отправляется сразу и ограничивается одним слайдом', () => {
      render(<InlinePresentationViewer html="x" isStreaming />);
      expect(mockSnapshot).toHaveBeenCalledWith('x', true, { maxReadyCount: 1 });
      const f = frame()!;
      const post = jest.spyOn(f.contentWindow!, 'postMessage');
      fireEvent.load(f);
      expect(post).toHaveBeenCalledWith({ type: 'astra-pptx-set-html', html: 'snap1' }, '*');
    });

    it('следующий слайд не отправляется раньше 700 мс и не «перепрыгивает» через слайды', () => {
      const { rerender } = render(<InlinePresentationViewer html="x" isStreaming />);
      fireEvent.load(frame()!);
      const post = jest.spyOn(frame()!.contentWindow!, 'postMessage');
      act(() => {
        jest.advanceTimersByTime(300);
      });
      rerender(<InlinePresentationViewer html="xy" isStreaming />);
      expect(post).not.toHaveBeenCalled();
      act(() => {
        jest.advanceTimersByTime(500);
      });
      rerender(<InlinePresentationViewer html="xyz" isStreaming />);
      expect(post).toHaveBeenCalledTimes(1);
      expect(post).toHaveBeenCalledWith({ type: 'astra-pptx-set-html', html: 'snap2' }, '*');
    });

    it('догоняющий таймер раз в 1.2 с дожимает по одному слайду, пока HTML готов дальше iframe', () => {
      render(<InlinePresentationViewer html="x" isStreaming />);
      fireEvent.load(frame()!);
      const post = jest.spyOn(frame()!.contentWindow!, 'postMessage');
      act(() => {
        jest.advanceTimersByTime(1200);
      });
      expect(post).toHaveBeenLastCalledWith({ type: 'astra-pptx-set-html', html: 'snap2' }, '*');
      act(() => {
        jest.advanceTimersByTime(1200);
      });
      expect(post).toHaveBeenLastCalledWith({ type: 'astra-pptx-set-html', html: 'snap3' }, '*');
      post.mockClear();
      act(() => {
        jest.advanceTimersByTime(2400);
      });
      expect(post).not.toHaveBeenCalled(); // дошли до 3 из 3
    });

    it('догоняющий таймер ничего не делает, если готовых слайдов не прибавилось', () => {
      fullReady = 1;
      render(<InlinePresentationViewer html="x" isStreaming />);
      fireEvent.load(frame()!);
      const post = jest.spyOn(frame()!.contentWindow!, 'postMessage');
      act(() => {
        jest.advanceTimersByTime(3000);
      });
      expect(post).not.toHaveBeenCalled();
    });

    it('догоняющий таймер уважает минимальный интервал между отправками', () => {
      // Дата заморожена вне таймера: modern fake timers двигают Date вместе с таймерами,
      // поэтому имитируем «слишком рано», подменив Date.now на момент последней отправки.
      render(<InlinePresentationViewer html="x" isStreaming />);
      fireEvent.load(frame()!);
      const post = jest.spyOn(frame()!.contentWindow!, 'postMessage');
      const realNow = Date.now;
      act(() => {
        jest.advanceTimersByTime(1100);
      });
      jest.spyOn(Date, 'now').mockReturnValue(realNow() - 1000);
      act(() => {
        jest.advanceTimersByTime(100);
      });
      expect(post).not.toHaveBeenCalled();
    });

    it('если новых готовых слайдов нет (даже спустя 700 мс), повторной отправки нет', () => {
      fullReady = 1;
      const { rerender } = render(<InlinePresentationViewer html="x" isStreaming />);
      fireEvent.load(frame()!);
      const post = jest.spyOn(frame()!.contentWindow!, 'postMessage');
      act(() => {
        jest.advanceTimersByTime(800);
      });
      mockSnapshot.mockClear();
      rerender(<InlinePresentationViewer html="xy" isStreaming />);
      expect(mockSnapshot).toHaveBeenCalledTimes(1); // только базовый снимок, усечённый не запрашивался
      expect(post).not.toHaveBeenCalled();
    });

    it('если усечённый снимок пуст — состояние не меняется', () => {
      mockSnapshot.mockImplementation((_h: string, _s: boolean, opts?: { maxReadyCount?: number }) =>
        opts ? { html: null, readyCount: 0, startedCount: 0 } : { html: 'x', readyCount: 2, startedCount: 2 },
      );
      render(<InlinePresentationViewer html="x" isStreaming />);
      expect(frame()).toBeNull();
      act(() => {
        jest.advanceTimersByTime(1500);
      });
      expect(frame()).toBeNull();
    });

    it('окончание стрима снимает «ожидание», показывает кнопки и итоговый статус', () => {
      const { rerender } = render(<InlinePresentationViewer html="x" isStreaming />);
      expect(screen.queryByLabelText('Открыть в новой вкладке')).toBeNull();
      rerender(<InlinePresentationViewer html="xfinal" isStreaming={false} />);
      expect(screen.getByText('Презентация')).toBeInTheDocument();
      expect(screen.getByLabelText('Открыть в новой вкладке')).toBeInTheDocument();
      expect(screen.queryByRole('progressbar')).toBeNull();
    });

    it('интервал догонялки останавливается после окончания стрима', () => {
      const { rerender } = render(<InlinePresentationViewer html="x" isStreaming />);
      rerender(<InlinePresentationViewer html="x" isStreaming={false} />);
      mockSnapshot.mockClear();
      act(() => {
        jest.advanceTimersByTime(5000);
      });
      expect(mockSnapshot).not.toHaveBeenCalled();
    });
  });

  describe('embedded', () => {
    it('без собственной шапки и рамки: только сцена с iframe', () => {
      render(<InlinePresentationViewer html="x" embedded />);
      expect(frame()).not.toBeNull();
      expect(screen.queryByText('Презентация')).toBeNull();
      expect(screen.queryByLabelText('Открыть в новой вкладке')).toBeNull();
    });

    it('embedded без слайдов показывает сообщение об ошибке', () => {
      fullHtml = null;
      render(<InlinePresentationViewer html="x" embedded />);
      expect(screen.getByText('Не удалось собрать слайды презентации')).toBeInTheDocument();
    });
  });
});
