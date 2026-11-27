import React, {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
} from 'react';
import type { ChatArtifact } from '../types/artifacts';

export type WorkspaceViewerSession =
  | { kind: 'artifact'; title: string; artifact: ChatArtifact }
  | { kind: 'presentation'; title: string; html: string };

type ChatWorkspaceViewerContextValue = {
  session: WorkspaceViewerSession | null;
  expand: (session: WorkspaceViewerSession) => void;
  collapse: () => void;
};

const ChatWorkspaceViewerContext = createContext<ChatWorkspaceViewerContextValue | null>(
  null,
);

export function useChatWorkspaceViewer(): ChatWorkspaceViewerContextValue {
  const ctx = useContext(ChatWorkspaceViewerContext);
  if (!ctx) {
    return {
      session: null,
      expand: () => {
        console.warn('ChatWorkspaceViewerProvider отсутствует — expand проигнорирован');
      },
      collapse: () => undefined,
    };
  }
  return ctx;
}

export function ChatWorkspaceViewerProvider({ children }: { children: React.ReactNode }) {
  const [session, setSession] = useState<WorkspaceViewerSession | null>(null);
  const expand = useCallback((next: WorkspaceViewerSession) => {
    setSession(next);
  }, []);
  const collapse = useCallback(() => setSession(null), []);
  const value = useMemo(
    () => ({ session, expand, collapse }),
    [session, expand, collapse],
  );
  return (
    <ChatWorkspaceViewerContext.Provider value={value}>
      {children}
    </ChatWorkspaceViewerContext.Provider>
  );
}
