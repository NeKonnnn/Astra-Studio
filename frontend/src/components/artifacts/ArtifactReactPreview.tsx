import React, { useMemo } from 'react';
import { Box } from '@mui/material';
import { SandpackProvider, SandpackPreview } from '@codesandbox/sandpack-react';
import { useCommittedContent } from '../../hooks/useCommittedContent';

interface Props {
  content: string;
  isStreaming?: boolean;
}

function normalizeReactEntry(raw: string): string {
  let code = (raw || '').trim();
  if (!code) {
    return `export default function Empty() { return <div>Empty</div>; }`;
  }
  // Убираем fence language leftovers
  if (code.startsWith('tsx') || code.startsWith('jsx') || code.startsWith('typescript')) {
    code = code.replace(/^(tsx|jsx|typescript|javascript)\s*\n/, '');
  }
  if (!/export\s+default/.test(code)) {
    // Если есть именованный компонент App / Component — экспортируем его
    const named = code.match(/(?:function|const)\s+([A-Z][A-Za-z0-9_]*)/);
    if (named) {
      code = `${code}\nexport default ${named[1]};`;
    } else {
      code = `export default function ArtifactApp() {\n  return (\n${code}\n  );\n}`;
    }
  }
  return code;
}

export default function ArtifactReactPreview({ content, isStreaming = false }: Props) {
  // Sandpack на каждый токен → remount Virtuoso → #185. Как HTML: превью только после стрима.
  const committed = useCommittedContent(content || '', isStreaming, isStreaming ? 1200 : 500);

  const files = useMemo(
    () => ({
      '/App.tsx': normalizeReactEntry(committed),
      '/index.tsx': `import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles.css";

const root = createRoot(document.getElementById("root")!);
root.render(<App />);
`,
      '/styles.css': `html, body, #root { margin: 0; min-height: 100%; }
body { font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; }
`,
      '/public/index.html': `<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <script src="https://cdn.tailwindcss.com"></script>
    <title>Artifact</title>
  </head>
  <body>
    <div id="root"></div>
  </body>
</html>
`,
    }),
    [committed],
  );

  // На стриме Sandpack не монтируем (remount → Virtuoso #185); статус — только в шапке карточки.
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
        options={{
          externalResources: ['https://cdn.tailwindcss.com'],
          recompileMode: 'delayed',
          recompileDelay: 400,
        }}
        customSetup={{
          dependencies: {
            'lucide-react': '^0.394.0',
            recharts: '2.12.7',
            three: '^0.167.1',
            'date-fns': '^3.3.1',
            'react-day-picker': '^8.10.1',
            clsx: '^2.1.1',
            'tailwind-merge': '^2.3.0',
            'class-variance-authority': '^0.7.0',
          },
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
