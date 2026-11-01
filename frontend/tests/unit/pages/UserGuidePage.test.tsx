import React from 'react';
import { render, screen } from '@testing-library/react';
import '@testing-library/jest-dom';
import UserGuidePage from '@/pages/UserGuidePage';

jest.mock('@/pages/StaticHtmlDocFrame', () => ({
  __esModule: true,
  default: ({ src, title }: { src: string; title: string }) => (
    <div data-testid="doc-frame" data-src={src} data-title={title} />
  ),
}));

describe('UserGuidePage', () => {
  const originalPublicUrl = process.env.PUBLIC_URL;

  afterEach(() => {
    process.env.PUBLIC_URL = originalPublicUrl;
  });

  it('открывает руководство из public/static/user_documentation с заголовком', () => {
    process.env.PUBLIC_URL = '';
    render(<UserGuidePage />);
    const frame = screen.getByTestId('doc-frame');
    expect(frame).toHaveAttribute('data-src', '/static/user_documentation/user-guide.html');
    expect(frame).toHaveAttribute('data-title', 'AstraChat — руководство пользователя');
  });

  it('учитывает PUBLIC_URL при развёртывании не в корне', () => {
    process.env.PUBLIC_URL = '/astra';
    render(<UserGuidePage />);
    expect(screen.getByTestId('doc-frame')).toHaveAttribute(
      'data-src',
      '/astra/static/user_documentation/user-guide.html',
    );
  });
});
