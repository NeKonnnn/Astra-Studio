import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { createTheme, ThemeProvider } from '@mui/material';
import StaticHtmlDocFrame from '@/pages/StaticHtmlDocFrame';

const mockNavigate = jest.fn();
jest.mock('react-router-dom', () => ({ useNavigate: () => mockNavigate }));
jest.mock('@/components/WorkZoneStarrySky', () => ({
  __esModule: true,
  default: () => <div data-testid="starry" />,
}));
jest.mock('@/components/WorkZoneSnowfall', () => ({
  __esModule: true,
  default: () => <div data-testid="snow" />,
}));

function okResponse(text: string, type = 'text/html; charset=utf-8') {
  return {
    ok: true,
    status: 200,
    headers: { get: () => type },
    text: async () => text,
  };
}

const BODY =
  '<a class="back" href="/" target="_parent" id="b">назад</a>' +
  '<a href="#sec" id="toSec">к секции</a><a href="#" id="top">вверх</a>' +
  '<a href="#nope" id="nope">нет</a><a href="https://x.y" id="ext">внешняя</a>' +
  '<a href="" id="empty">пусто</a><p id="sec">секция</p><span id="plain">t</span>';
const DOC_HTML = `<html><body>${BODY}</body></html>`;

async function renderFrame(opts: { dark?: boolean } = {}) {
  const theme = createTheme({ palette: { mode: opts.dark ? 'dark' : 'light' } });
  const utils = render(
    <ThemeProvider theme={theme}>
      <StaticHtmlDocFrame src="/doc.html" title="Док" />
    </ThemeProvider>,
  );
  await waitFor(() => expect(screen.getByTitle('Док')).toBeTruthy());
  return utils;
}

/** Подменяет contentDocument iframe настоящим документом и стреляет load. */
function loadIframe() {
  const iframe = screen.getByTitle('Док') as HTMLIFrameElement;
  const doc = document.implementation.createHTMLDocument('x');
  doc.body.innerHTML = BODY;
  const origRemove = doc.removeEventListener.bind(doc);
  doc.removeEventListener = jest.fn(origRemove) as typeof doc.removeEventListener;
  Object.defineProperty(iframe, 'contentDocument', { configurable: true, get: () => doc });
  fireEvent.load(iframe);
  return { iframe, doc };
}

const click = (el: Element) => {
  const ev = new MouseEvent('click', { bubbles: true, cancelable: true });
  el.dispatchEvent(ev);
  return ev;
};

describe('StaticHtmlDocFrame', () => {
  const fetchMock = jest.fn();
  beforeEach(() => {
    localStorage.clear();
    mockNavigate.mockClear();
    fetchMock.mockReset();
    (global as any).fetch = fetchMock;
  });

  it('грузит документ, убирает target у ссылки .back и кладёт в srcDoc', async () => {
    fetchMock.mockResolvedValue(okResponse(DOC_HTML));
    await renderFrame();
    expect(fetchMock).toHaveBeenCalledWith('/doc.html', { credentials: 'same-origin' });
    const srcDoc = (screen.getByTitle('Док') as HTMLIFrameElement).getAttribute('srcdoc') || '';
    expect(srcDoc).toContain('class="back"');
    expect(srcDoc).not.toContain('target="_parent"');
  });

  it('при HTTP-ошибке показывает сообщение вместо iframe', async () => {
    fetchMock.mockResolvedValue({ ok: false, status: 404, headers: { get: () => '' } });
    render(<StaticHtmlDocFrame src="/doc.html" title="Док" />);
    expect(await screen.findByText('Не удалось загрузить документ справки')).toBeTruthy();
    expect(screen.queryByTitle('Док')).toBeNull();
  });

  it('при неподходящем content-type показывает ошибку', async () => {
    fetchMock.mockResolvedValue(okResponse('{}', 'application/json'));
    render(<StaticHtmlDocFrame src="/doc.html" title="Док" />);
    expect(await screen.findByText('Не удалось загрузить документ справки')).toBeTruthy();
  });

  it('пустой content-type допускается', async () => {
    fetchMock.mockResolvedValue(okResponse(DOC_HTML, ''));
    await renderFrame();
    expect(screen.getByTitle('Док')).toBeTruthy();
  });

  it('при ошибке сети показывает сообщение', async () => {
    fetchMock.mockRejectedValue(new Error('net'));
    render(<StaticHtmlDocFrame src="/doc.html" title="Док" />);
    expect(await screen.findByText('Не удалось загрузить документ справки')).toBeTruthy();
  });

  it('после загрузки применяет светлую тему и скрывает скроллбар в документе', async () => {
    fetchMock.mockResolvedValue(okResponse(DOC_HTML));
    await renderFrame();
    const { doc, iframe } = loadIframe();
    const root = doc.documentElement;
    expect(root.style.getPropertyValue('color-scheme')).toBe('light');
    expect(root.style.getPropertyValue('--fg')).toBe('#111111');
    expect(root.style.getPropertyValue('--bg')).toBe('transparent');
    expect(doc.body.style.background).toBe('transparent');
    expect(doc.getElementById('astra-doc-scrollbar-hide')).not.toBeNull();
    // повторная загрузка не плодит <style>
    fireEvent.load(iframe);
    expect(doc.querySelectorAll('#astra-doc-scrollbar-hide').length).toBe(1);
  });

  it('в тёмной теме выставляет тёмные переменные', async () => {
    fetchMock.mockResolvedValue(okResponse(DOC_HTML));
    await renderFrame({ dark: true });
    const { doc } = loadIframe();
    expect(doc.documentElement.style.getPropertyValue('color-scheme')).toBe('dark');
    expect(doc.documentElement.style.getPropertyValue('--fg')).toBe('#f2f2f2');
  });

  it('клик по ссылке .back / пустой href ведёт на корень SPA и блокирует переход', async () => {
    fetchMock.mockResolvedValue(okResponse(DOC_HTML));
    await renderFrame();
    const { doc } = loadIframe();
    expect(click(doc.getElementById('b')!).defaultPrevented).toBe(true);
    expect(click(doc.getElementById('empty')!).defaultPrevented).toBe(true);
    expect(mockNavigate).toHaveBeenCalledTimes(2);
    expect(mockNavigate).toHaveBeenCalledWith('/');
  });

  it('якорь прокручивает к элементу; "#" — наверх; неизвестный якорь и внешняя ссылка обработаны', async () => {
    fetchMock.mockResolvedValue(okResponse(DOC_HTML));
    await renderFrame();
    const { doc } = loadIframe();
    const sec = doc.getElementById('sec')!;
    sec.scrollIntoView = jest.fn();
    const scrollTo = jest.fn();
    Object.defineProperty(doc, 'defaultView', { configurable: true, get: () => ({ scrollTo }) });

    expect(click(doc.getElementById('toSec')!).defaultPrevented).toBe(true);
    expect(sec.scrollIntoView).toHaveBeenCalledWith({ behavior: 'smooth', block: 'start' });

    expect(click(doc.getElementById('top')!).defaultPrevented).toBe(true);
    expect(scrollTo).toHaveBeenCalledWith({ top: 0, behavior: 'smooth' });

    expect(click(doc.getElementById('nope')!).defaultPrevented).toBe(true);
    expect(sec.scrollIntoView).toHaveBeenCalledTimes(1);

    // внешняя ссылка не перехватывается; клик не по ссылке игнорируется
    expect(click(doc.getElementById('ext')!).defaultPrevented).toBe(false);
    expect(click(doc.getElementById('plain')!).defaultPrevented).toBe(false);
    expect(mockNavigate).not.toHaveBeenCalled();
  });

  it('при повторной загрузке iframe старый перехватчик кликов снимается', async () => {
    fetchMock.mockResolvedValue(okResponse(DOC_HTML));
    await renderFrame();
    const { iframe, doc } = loadIframe();
    expect(doc.removeEventListener).not.toHaveBeenCalled();
    fireEvent.load(iframe);
    expect(doc.removeEventListener).toHaveBeenCalledWith('click', expect.any(Function), true);
    click(doc.getElementById('b')!);
    expect(mockNavigate).toHaveBeenCalledTimes(1);
  });

  it('если у iframe нет contentDocument, load ничего не ломает', async () => {
    fetchMock.mockResolvedValue(okResponse(DOC_HTML));
    await renderFrame();
    const iframe = screen.getByTitle('Док') as HTMLIFrameElement;
    Object.defineProperty(iframe, 'contentDocument', { configurable: true, get: () => null });
    expect(() => fireEvent.load(iframe)).not.toThrow();
  });

  it('ответ, пришедший после размонтирования, игнорируется', async () => {
    let resolve!: (v: unknown) => void;
    fetchMock.mockReturnValue(new Promise((r) => { resolve = r; }));
    const { unmount } = render(<StaticHtmlDocFrame src="/doc.html" title="Док" />);
    unmount();
    await act(async () => { resolve(okResponse(DOC_HTML)); });
    expect(screen.queryByTitle('Док')).toBeNull();
  });

  it('размонтирование после загрузки снимает обработчик кликов', async () => {
    fetchMock.mockResolvedValue(okResponse(DOC_HTML));
    const { unmount } = await renderFrame();
    const { doc } = loadIframe();
    expect(doc.removeEventListener).not.toHaveBeenCalled();
    unmount();
    expect(doc.removeEventListener).toHaveBeenCalledWith('click', expect.any(Function), true);
  });

  it('при смене темы в уже открытом iframe переменные обновляются', async () => {
    fetchMock.mockResolvedValue(okResponse(DOC_HTML));
    const mk = (mode: 'light' | 'dark') => (
      <ThemeProvider theme={createTheme({ palette: { mode } })}>
        <StaticHtmlDocFrame src="/doc.html" title="Док" />
      </ThemeProvider>
    );
    const { rerender } = render(mk('light'));
    await waitFor(() => expect(screen.getByTitle('Док')).toBeTruthy());
    const { doc } = loadIframe();
    expect(doc.documentElement.style.getPropertyValue('--fg')).toBe('#111111');
    rerender(mk('dark'));
    expect(doc.documentElement.style.getPropertyValue('--fg')).toBe('#f2f2f2');
  });

  it('режимы фона: звёздное небо и снегопад рисуют свои слои', async () => {
    fetchMock.mockResolvedValue(okResponse(DOC_HTML));
    localStorage.setItem('work_zone_bg_mode', 'starry');
    const a = await renderFrame();
    expect(screen.getByTestId('starry')).toBeTruthy();
    a.unmount();
    localStorage.setItem('work_zone_bg_mode', 'snowfall');
    await renderFrame();
    expect(screen.getByTestId('snow')).toBeTruthy();
  });

  it('пользовательская картинка применяется и обновляется по interfaceSettingsChanged', async () => {
    fetchMock.mockResolvedValue(okResponse(DOC_HTML));
    localStorage.setItem('work_zone_bg_custom_image', 'data:img1');
    localStorage.setItem('work_zone_bg_mode', 'custom');
    const { container } = await renderFrame();
    expect(getComputedStyle(container.firstChild as Element).backgroundImage).toContain('data:img1');
    act(() => {
      localStorage.setItem('work_zone_bg_custom_image', 'data:img2');
      window.dispatchEvent(new Event('interfaceSettingsChanged'));
    });
    expect(getComputedStyle(container.firstChild as Element).backgroundImage).toContain('data:img2');
  });
});
