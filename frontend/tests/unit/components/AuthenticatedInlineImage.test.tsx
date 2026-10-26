/**
 * @jest-environment jsdom
 */
import React from 'react';
import { act, render, screen, waitFor } from '@testing-library/react';
import AuthenticatedInlineImage from '@/components/AuthenticatedInlineImage';

const mockAuthFetch = jest.fn();

jest.mock('@/config/api', () => ({
  authFetch: (...args: unknown[]) => mockAuthFetch(...args),
  getApiUrl: (p: string) => `https://api.example${p}`,
}));

jest.mock('@/contexts/AuthContext', () => ({
  useAuth: () => ({ token: 'jwt-test' }),
}));

describe('AuthenticatedInlineImage', () => {
  beforeEach(() => {
    mockAuthFetch.mockReset();
    URL.createObjectURL = jest.fn(() => 'blob:preview-1') as unknown as typeof URL.createObjectURL;
    URL.revokeObjectURL = jest.fn() as unknown as typeof URL.revokeObjectURL;
  });

  it('uses data: URL as-is without authFetch', () => {
    render(
      <AuthenticatedInlineImage src="data:image/png;base64,abc" alt="gen" />,
    );
    expect(screen.getByAltText('gen')).toHaveAttribute('src', 'data:image/png;base64,abc');
    expect(mockAuthFetch).not.toHaveBeenCalled();
  });

  it('loads MinIO/inline-file via authFetch (credentials cookie binding)', async () => {
    mockAuthFetch.mockResolvedValue({
      ok: true,
      blob: async () => new Blob(['x'], { type: 'image/png' }),
    });

    await act(async () => {
      render(
        <AuthenticatedInlineImage
          src="/api/documents/inline-file?bucket=b&object=o"
          alt="minio"
        />,
      );
    });

    await waitFor(() => {
      expect(mockAuthFetch).toHaveBeenCalledWith(
        'https://api.example/api/documents/inline-file?bucket=b&object=o',
      );
    });
    await waitFor(() => {
      expect(screen.getByAltText('minio')).toHaveAttribute('src', 'blob:preview-1');
    });
  });
});
