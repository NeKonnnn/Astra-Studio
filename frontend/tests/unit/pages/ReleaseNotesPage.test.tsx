import React from 'react';
import { render } from '@testing-library/react';
import ReleaseNotesPage from '@/pages/ReleaseNotesPage';

const mockFrame = jest.fn();
jest.mock('@/pages/StaticHtmlDocFrame', () => ({
  __esModule: true,
  default: (props: { src: string; title: string }) => {
    mockFrame(props);
    return <div data-testid="frame" />;
  },
}));

describe('ReleaseNotesPage', () => {
  const oldPublicUrl = process.env.PUBLIC_URL;
  afterEach(() => {
    process.env.PUBLIC_URL = oldPublicUrl;
    mockFrame.mockClear();
  });

  it('передаёт во фрейм документ релиза 2.0 с PUBLIC_URL', () => {
    process.env.PUBLIC_URL = '/app';
    render(<ReleaseNotesPage version="2.0" />);
    expect(mockFrame).toHaveBeenCalledWith({
      src: '/app/static/user_documentation/astrachat-release-2.0.html',
      title: 'AstraChat 2.0 — подробности релиза',
    });
  });

  it('для версии 1.0 без PUBLIC_URL строит относительный путь', () => {
    process.env.PUBLIC_URL = '';
    render(<ReleaseNotesPage version="1.0" />);
    expect(mockFrame).toHaveBeenCalledWith({
      src: '/static/user_documentation/astrachat-release-1.0.html',
      title: 'AstraChat 1.0 — подробности релиза',
    });
  });

  it('неизвестная версия откатывается на релиз 1.0', () => {
    process.env.PUBLIC_URL = '';
    render(<ReleaseNotesPage version="9.9" />);
    expect(mockFrame.mock.calls[0][0].src).toContain('astrachat-release-1.0.html');
  });
});
