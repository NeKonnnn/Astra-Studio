/**
 * Каркас тестов MessageRenderer: моки тяжёлых зависимостей (Monaco, xlsx, карточки артефактов,
 * презентация, гейты viewer). Импортировать ПЕРВЫМ, до MessageRenderer (jest.mock должны сработать раньше).
 * react-markdown здесь не используется: MessageRenderer разбирает markdown собственным кодом.
 */
import React from 'react';

export const mockHx: {
  liveGate: boolean;
  presentationExpected: boolean;
  artifactsAllowed: boolean;
  currentChatId: string | null;
  lastAllowedArgs: any;
  pin: jest.Mock;
  writeFile: jest.Mock;
  editorLayout: jest.Mock;
  defineTheme: jest.Mock;
} = {
  liveGate: false,
  presentationExpected: false,
  artifactsAllowed: false,
  currentChatId: 'chat-1',
  lastAllowedArgs: null,
  pin: jest.fn(),
  writeFile: jest.fn(),
  editorLayout: jest.fn(),
  defineTheme: jest.fn(),
};

export function resetHx() {
  mockHx.liveGate = false;
  mockHx.presentationExpected = false;
  mockHx.artifactsAllowed = false;
  mockHx.currentChatId = 'chat-1';
  mockHx.lastAllowedArgs = null;
  mockHx.pin.mockReset();
  mockHx.writeFile.mockReset();
  mockHx.editorLayout.mockReset();
  mockHx.defineTheme.mockReset();
}

jest.mock('../contexts/AppContext', () => ({
  useAppContext: () => ({ state: { currentChatId: mockHx.currentChatId } }),
}));

jest.mock('../hooks/useArtifactsViewerGate', () => ({
  useArtifactsViewerAllowed: (args: any) => {
    mockHx.lastAllowedArgs = args;
    return mockHx.artifactsAllowed;
  },
  useArtifactsViewerLiveGate: () => mockHx.liveGate,
  usePresentationViewerExpected: () => mockHx.presentationExpected,
}));

jest.mock('../utils/messageArtifactsViewerStorage', () => ({
  pinMessageArtifactsViewer: (...a: unknown[]) => mockHx.pin(...a),
}));

jest.mock('@monaco-editor/react', () => {
  const R = require('react');
  const Editor = (p: any) => {
    R.useEffect(() => {
      p.beforeMount?.({ editor: { defineTheme: (...a: unknown[]) => mockHx.defineTheme(...a) } });
      p.onMount?.({ layout: () => mockHx.editorLayout() });
    }, []);
    return (
      <div
        data-testid="monaco"
        data-language={p.language}
        data-path={p.path}
        data-height={p.height}
        data-readonly={String(p.options?.readOnly)}
        data-linenumbers={p.options?.lineNumbers}
      >
        {p.value}
      </div>
    );
  };
  return { __esModule: true, default: Editor, loader: { config: jest.fn(), init: jest.fn() } };
});

jest.mock('xlsx', () => ({
  utils: {
    book_new: () => ({ sheets: [] as unknown[] }),
    aoa_to_sheet: (data: unknown[][]) => ({ data }),
    book_append_sheet: (wb: { sheets: unknown[] }, ws: unknown, name: string) => wb.sheets.push({ ws, name }),
  },
  writeFile: (...a: unknown[]) => mockHx.writeFile(...a),
}));

jest.mock('./artifacts/ArtifactCard', () => ({
  __esModule: true,
  default: (p: any) => (
    <div
      data-testid="artifact-card"
      data-type={p.artifact.type}
      data-closed={String(p.artifact.closed)}
      data-streaming={String(Boolean(p.isStreaming))}
      data-id={p.artifact.id}
      data-title={p.artifact.title}
    >
      {p.artifact.content}
    </div>
  ),
}));

jest.mock('./InlinePresentationViewer', () => ({
  __esModule: true,
  default: (p: any) => (
    <div
      data-testid="presentation"
      data-streaming={String(Boolean(p.isStreaming))}
      data-has-source={String(Boolean(p.sourceSlot))}
    >
      {p.html}
      {p.sourceSlot}
    </div>
  ),
}));
