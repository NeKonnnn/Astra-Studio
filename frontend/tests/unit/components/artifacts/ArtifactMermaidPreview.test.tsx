/**
 * @jest-environment jsdom
 */
import React from 'react';
import { render, waitFor } from '@testing-library/react';
import ArtifactMermaidPreview from '@/components/artifacts/ArtifactMermaidPreview';

const mockInitialize = jest.fn();
const mockParse = jest.fn();
const mockRender = jest.fn();

jest.mock(
  'mermaid',
  () => ({
    __esModule: true,
    default: {
      initialize: (...args: unknown[]) => mockInitialize(...args),
      parse: (...args: unknown[]) => mockParse(...args),
      render: (...args: unknown[]) => mockRender(...args),
    },
  }),
  { virtual: true },
);

describe('ArtifactMermaidPreview mermaid security level', () => {
  beforeEach(() => {
    mockInitialize.mockReset();
    mockParse.mockReset();
    mockRender.mockReset();
    mockParse.mockResolvedValue(true);
    mockRender.mockResolvedValue({ svg: '<svg id="rendered"></svg>' });
  });

  it('initializes mermaid with a strict (non-loose) securityLevel', async () => {
    render(<ArtifactMermaidPreview content={'graph TD; A-->B;'} />);

    await waitFor(() => expect(mockInitialize).toHaveBeenCalledTimes(1));
    const config = mockInitialize.mock.calls[0][0];
    expect(config.securityLevel).toBe('strict');
    expect(config.securityLevel).not.toBe('loose');
    expect(config.startOnLoad).toBe(false);
  });
});
