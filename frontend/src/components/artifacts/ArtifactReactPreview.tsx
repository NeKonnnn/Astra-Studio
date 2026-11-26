import React, { useMemo, useState, useEffect, useCallback } from 'react';
import { Box } from '@mui/material';
import { SandpackProvider, SandpackPreview } from '@codesandbox/sandpack-react';
import { useCommittedContent } from '../../hooks/useCommittedContent';
import {
  REACT_ARTIFACT_DEPENDENCIES,
  buildSandpackFiles,
  buildSandpackOptions,
} from '../../utils/sandpackArtifacts';
import {
  ARTIFACTS_SELECTION_CHANGED_EVENT,
  getEffectiveArtifactsSettings,
} from '../../utils/artifactsSelectionStorage';
import { AGENT_ARTIFACTS_CHANGED_EVENT } from '../../utils/agentArtifactsEnabled';
import { useAppContext } from '../../contexts/AppContext';

interface Props {
  content: string;
  isStreaming?: boolean;
}

/**
 * React-артефакт через Sandpack (как LibreChat): shadcn shared files при shadcn_enabled.
 * На стриме не монтируем (remount → Virtuoso #185).
 */
export default function ArtifactReactPreview({ content, isStreaming = false }: Props) {
  const { state: appState } = useAppContext();
  const chatId = appState.currentChatId;
  const committed = useCommittedContent(content || '', isStreaming, isStreaming ? 1200 : 500);

  const readShadcn = useCallback(
    () => Boolean(getEffectiveArtifactsSettings(chatId).shadcn_enabled),
    [chatId],
  );
  const [includeShadcn, setIncludeShadcn] = useState(readShadcn);

  useEffect(() => {
    const sync = () => setIncludeShadcn(readShadcn());
    sync();
    window.addEventListener(ARTIFACTS_SELECTION_CHANGED_EVENT, sync as EventListener);
    window.addEventListener(AGENT_ARTIFACTS_CHANGED_EVENT, sync);
    window.addEventListener('agentSelected', sync as EventListener);
    window.addEventListener('storage', sync);
    return () => {
      window.removeEventListener(ARTIFACTS_SELECTION_CHANGED_EVENT, sync as EventListener);
      window.removeEventListener(AGENT_ARTIFACTS_CHANGED_EVENT, sync);
      window.removeEventListener('agentSelected', sync as EventListener);
      window.removeEventListener('storage', sync);
    };
  }, [readShadcn]);

  const files = useMemo(
    () => buildSandpackFiles(committed, { includeShadcn }),
    [committed, includeShadcn],
  );
  const options = useMemo(() => buildSandpackOptions(), []);
  const dependencies = useMemo(
    () =>
      includeShadcn
        ? REACT_ARTIFACT_DEPENDENCIES
        : {
            'lucide-react': REACT_ARTIFACT_DEPENDENCIES['lucide-react'],
            recharts: REACT_ARTIFACT_DEPENDENCIES.recharts,
            three: REACT_ARTIFACT_DEPENDENCIES.three,
            'date-fns': REACT_ARTIFACT_DEPENDENCIES['date-fns'],
            'react-day-picker': REACT_ARTIFACT_DEPENDENCIES['react-day-picker'],
            clsx: REACT_ARTIFACT_DEPENDENCIES.clsx,
            'tailwind-merge': REACT_ARTIFACT_DEPENDENCIES['tailwind-merge'],
            'class-variance-authority': REACT_ARTIFACT_DEPENDENCIES['class-variance-authority'],
          },
    [includeShadcn],
  );

  if (isStreaming) {
    return <Box sx={{ width: '100%', height: '100%', minHeight: 240, bgcolor: '#e8eaed' }} />;
  }

  if (!(committed || '').trim()) {
    return <Box sx={{ height: '100%', minHeight: 240, bgcolor: '#e8eaed' }} />;
  }

  return (
    <Box sx={{ height: '100%', minHeight: 360, '& .sp-wrapper': { height: '100%' } }}>
      <SandpackProvider
        template="react-ts"
        theme="light"
        files={files}
        options={options}
        customSetup={{
          dependencies,
        }}
      >
        <SandpackPreview
          showOpenInCodeSandbox={false}
          showRefreshButton
          style={{ height: '100%', minHeight: 360 }}
        />
      </SandpackProvider>
    </Box>
  );
}
