import {
  buildReactPreviewSrcDoc,
  escapeForScriptTemplate,
  hasExternalReactImports,
  normalizeReactEntry,
} from '@/utils/reactPreview';

const countOccurrences = (haystack: string, needle: string): number =>
  haystack.split(needle).length - 1;

describe('reactPreview react imports', () => {
  it('strips `import React, { useState } from "react"` and destructures hooks', () => {
    const doc = buildReactPreviewSrcDoc(
      `import React, { useState } from 'react';\nexport default function App() { const [n] = useState(0); return <div>{n}</div>; }`,
    );
    expect(doc).not.toMatch(/import\s+React/);
    expect(doc).not.toContain("from 'react'");
    expect(doc).toContain('const { useState } = React;');
    // React уже в scope — не должно быть `const { React } = React`
    expect(doc).not.toContain('{ React }');
  });

  it('handles default + multiple named imports with double quotes and no semicolon', () => {
    const doc = buildReactPreviewSrcDoc(
      `import React, { useState, useEffect } from "react"\nexport default function App() { return <div/>; }`,
    );
    expect(doc).not.toContain('from "react"');
    expect(doc).toContain('const { useState, useEffect } = React;');
  });

  it('still handles named-only and default-only imports', () => {
    const named = buildReactPreviewSrcDoc(
      `import { useRef } from 'react';\nexport default function App() { return <div/>; }`,
    );
    expect(named).toContain('const { useRef } = React;');
    expect(named).not.toContain("from 'react'");

    const def = buildReactPreviewSrcDoc(
      `import React from 'react';\nexport default function App() { return <div/>; }`,
    );
    expect(def).not.toContain("from 'react'");
    expect(def).not.toContain('{ React }');
  });

  it('maps `as` aliases to destructuring renames and skips type-only specifiers', () => {
    const doc = buildReactPreviewSrcDoc(
      `import React, { useState as useS, type FC } from 'react';\nexport default function App() { return <div/>; }`,
    );
    expect(doc).toContain('const { useState: useS } = React;');
    expect(doc).not.toContain('type FC');
  });

  it('binds namespace/other default aliases to React', () => {
    const doc = buildReactPreviewSrcDoc(
      `import * as R from 'react';\nexport default function App() { return <div/>; }`,
    );
    expect(doc).toContain('const R = React;');
    expect(doc).not.toContain("from 'react'");
  });

  it('removes react-dom imports and external imports', () => {
    const doc = buildReactPreviewSrcDoc(
      `import { createRoot } from 'react-dom/client';\nimport _ from 'lodash';\nexport default function App() { return <div/>; }`,
    );
    expect(doc).not.toContain("from 'react-dom/client'");
    expect(doc).not.toContain("from 'lodash'");
  });

  it('does not flag react imports as external', () => {
    expect(hasExternalReactImports(`import React, { useState } from 'react';`)).toBe(false);
    expect(hasExternalReactImports(`import _ from 'lodash';`)).toBe(true);
  });
});

describe('reactPreview escapeForScriptTemplate', () => {
  it('escapes </script> so it cannot terminate the inline script', () => {
    expect(escapeForScriptTemplate('a</script>b')).toBe('a<\\/script>b');
  });

  it('escapes </SCRIPT with any case and whitespace-less variants', () => {
    expect(escapeForScriptTemplate('</SCRIPT>')).toBe('<\\/SCRIPT>');
    expect(escapeForScriptTemplate('</Script >')).toBe('<\\/Script >');
  });

  it('still escapes backslashes, backticks and ${', () => {
    expect(escapeForScriptTemplate('\\ ` ${x}')).toBe('\\\\ \\` \\${x}');
  });

  it('escapes HTML comment openers', () => {
    expect(escapeForScriptTemplate('<!-- x')).toBe('<\\!-- x');
  });

  it('does not let user code add extra closing script tags to the srcdoc', () => {
    const clean = buildReactPreviewSrcDoc(
      `export default function App() { return <div>ok</div>; }`,
    );
    const evil = buildReactPreviewSrcDoc(
      `export default function App() { return <div>{"</script><img src=x onerror=alert(1)>"}</div>; }`,
    );
    expect(countOccurrences(evil, '</script>')).toBe(countOccurrences(clean, '</script>'));
    expect(evil).toContain('<\\/script>');
  });
});

describe('reactPreview normalizeReactEntry', () => {
  it('returns a placeholder component for empty input', () => {
    expect(normalizeReactEntry('')).toContain('export default function Empty');
  });

  it('appends default export for a named component', () => {
    expect(normalizeReactEntry('function Foo() { return null; }')).toContain('export default Foo;');
  });
});
