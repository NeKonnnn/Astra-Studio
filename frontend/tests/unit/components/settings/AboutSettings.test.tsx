import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import AboutSettings from '@/components/settings/AboutSettings';

const setUserAgent = (ua: string) =>
  Object.defineProperty(window.navigator, 'userAgent', { value: ua, configurable: true });

const CHROME_UA =
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36';
const FIREFOX_UA = 'Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0';
const SAFARI_UA =
  'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17 Safari/605.1.15';

const originalUA = window.navigator.userAgent;

afterEach(() => {
  setUserAgent(originalUA);
  jest.restoreAllMocks();
});

describe('AboutSettings', () => {
  it('renders static application info and technical info', () => {
    render(<AboutSettings />);

    expect(screen.getByText('Информация о приложении')).toBeTruthy();
    expect(screen.getByText('Web Interface v1.0.3')).toBeTruthy();
    expect(screen.getByText('FastAPI + Python')).toBeTruthy();
    expect(screen.getByText('React + TypeScript')).toBeTruthy();
    expect(screen.getByText('Socket.IO')).toBeTruthy();
    expect(screen.getByText('MIT License')).toBeTruthy();
    expect(screen.getByText('Активен')).toBeTruthy();
  });

  it('does not show the resources card nor an error alert while no metrics are available', () => {
    render(<AboutSettings />);

    expect(screen.queryByText('Системные ресурсы')).toBeNull();
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it.each([
    ['Chrome', CHROME_UA, 'Chrome v126'],
    ['Firefox', FIREFOX_UA, 'Firefox v128'],
    ['Safari', SAFARI_UA, 'Safari v17'],
  ])('detects the %s browser and its major version', (_name, ua, expected) => {
    setUserAgent(ua);
    render(<AboutSettings />);

    expect(screen.getByText(expected)).toBeTruthy();
  });

  it('shows Unknown for an unrecognised user agent', () => {
    setUserAgent('curl/8.0');
    render(<AboutSettings />);

    expect(screen.getByText('Unknown')).toBeTruthy();
  });

  it('detects Edge only when the user agent has the legacy "Edge/" token without Chrome/Safari', () => {
    setUserAgent('Mozilla/5.0 (Windows NT 10.0) Edge/18');
    render(<AboutSettings />);

    expect(screen.getByText('Edge v18')).toBeTruthy();
  });

  it('(known issue) reports modern Chromium Edge ("Edg/") as Chrome', () => {
    // Современный Edge содержит "Chrome/" в UA, поэтому ветка Edge для него недостижима.
    setUserAgent(`${CHROME_UA} Edg/126.0.0.0`);
    render(<AboutSettings />);

    expect(screen.getByText('Chrome v126')).toBeTruthy();
    expect(screen.queryByText(/Edge/)).toBeNull();
  });

  it('falls back to the placeholder platform when navigator.platform is empty', () => {
    jest.spyOn(window.navigator, 'platform', 'get').mockReturnValue('');
    render(<AboutSettings />);

    expect(screen.getByText('Неизвестно')).toBeTruthy();
  });

  it('refresh button re-runs the loader and stays enabled afterwards', () => {
    render(<AboutSettings />);
    const button = screen.getByRole('button', { name: /Обновить информацию/ }) as HTMLButtonElement;

    fireEvent.click(button);

    expect(button.disabled).toBe(false);
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('exports the system info as system-info.json with browser details and timestamp', async () => {
    setUserAgent(CHROME_UA);
    let blob: Blob | undefined;
    (URL as any).createObjectURL = jest.fn((b: Blob) => {
      blob = b;
      return 'blob:fake';
    });
    (URL as any).revokeObjectURL = jest.fn();
    const clicked: HTMLAnchorElement[] = [];
    jest.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      clicked.push(this);
    });

    render(<AboutSettings />);
    fireEvent.click(screen.getByRole('button', { name: /Экспорт информации/ }));

    expect(clicked).toHaveLength(1);
    expect(clicked[0].download).toBe('system-info.json');
    expect(clicked[0].href).toBe('blob:fake');
    expect((URL as any).revokeObjectURL).toHaveBeenCalledWith('blob:fake');
    expect(blob!.type).toBe('application/json');

    const text = await new Promise<string>((resolve) => {
      const r = new FileReader();
      r.onload = () => resolve(String(r.result));
      r.readAsText(blob!);
    });
    const parsed = JSON.parse(text);
    expect(parsed.version).toBe('Web Interface v1.0.3');
    expect(parsed.browser).toEqual({ name: 'Chrome', version: '126' });
    expect(Number.isNaN(Date.parse(parsed.timestamp))).toBe(false);
  });
});
