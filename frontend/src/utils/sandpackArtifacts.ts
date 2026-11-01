import type { SandpackProviderProps } from '@codesandbox/sandpack-react';
import { shadcnComponents } from './sandpackShadcn.generated';
import { normalizeReactEntry } from './reactPreview';

/** Зависимости Sandpack для React-артефактов (как в LibreChat). */
export const REACT_ARTIFACT_DEPENDENCIES: Record<string, string> = {
  three: '^0.167.1',
  'lucide-react': '^0.394.0',
  'react-router-dom': '^6.11.2',
  'class-variance-authority': '^0.6.0',
  clsx: '^1.2.1',
  'date-fns': '^3.3.1',
  'tailwind-merge': '^1.9.1',
  'tailwindcss-animate': '^1.0.5',
  recharts: '2.12.7',
  '@radix-ui/react-accordion': '^1.1.2',
  '@radix-ui/react-alert-dialog': '^1.0.2',
  '@radix-ui/react-aspect-ratio': '^1.1.0',
  '@radix-ui/react-avatar': '^1.1.0',
  '@radix-ui/react-checkbox': '^1.0.3',
  '@radix-ui/react-collapsible': '^1.0.3',
  '@radix-ui/react-dialog': '^1.0.2',
  '@radix-ui/react-dropdown-menu': '^2.1.1',
  '@radix-ui/react-hover-card': '^1.0.5',
  '@radix-ui/react-label': '^2.0.0',
  '@radix-ui/react-menubar': '^1.1.1',
  '@radix-ui/react-navigation-menu': '^1.2.0',
  '@radix-ui/react-popover': '^1.0.7',
  '@radix-ui/react-progress': '^1.1.0',
  '@radix-ui/react-radio-group': '^1.1.3',
  '@radix-ui/react-select': '^2.0.0',
  '@radix-ui/react-separator': '^1.0.3',
  '@radix-ui/react-slider': '^1.1.1',
  '@radix-ui/react-switch': '^1.0.3',
  '@radix-ui/react-tabs': '^1.0.3',
  '@radix-ui/react-toast': '^1.1.5',
  '@radix-ui/react-slot': '^1.1.0',
  '@radix-ui/react-toggle': '^1.1.0',
  '@radix-ui/react-toggle-group': '^1.1.0',
  '@radix-ui/react-tooltip': '^1.2.8',
  'embla-carousel-react': '^8.2.0',
  'react-day-picker': '^9.0.8',
  'dat.gui': '^0.7.9',
  vaul: '^0.9.1',
};

const TAILWIND_CDN = 'https://cdn.tailwindcss.com/3.4.17#tailwind.js';

export const SANDPACK_SHARED_FILES: SandpackProviderProps['files'] = {
  '/lib/utils.ts': shadcnComponents.utils,
  '/components/ui/accordion.tsx': shadcnComponents.accordian,
  '/components/ui/alert-dialog.tsx': shadcnComponents.alertDialog,
  '/components/ui/alert.tsx': shadcnComponents.alert,
  '/components/ui/avatar.tsx': shadcnComponents.avatar,
  '/components/ui/badge.tsx': shadcnComponents.badge,
  '/components/ui/breadcrumb.tsx': shadcnComponents.breadcrumb,
  '/components/ui/button.tsx': shadcnComponents.button,
  '/components/ui/calendar.tsx': shadcnComponents.calendar,
  '/components/ui/card.tsx': shadcnComponents.card,
  '/components/ui/carousel.tsx': shadcnComponents.carousel,
  '/components/ui/checkbox.tsx': shadcnComponents.checkbox,
  '/components/ui/collapsible.tsx': shadcnComponents.collapsible,
  '/components/ui/dialog.tsx': shadcnComponents.dialog,
  '/components/ui/drawer.tsx': shadcnComponents.drawer,
  '/components/ui/dropdown-menu.tsx': shadcnComponents.dropdownMenu,
  '/components/ui/input.tsx': shadcnComponents.input,
  '/components/ui/label.tsx': shadcnComponents.label,
  '/components/ui/menubar.tsx': shadcnComponents.menuBar,
  '/components/ui/navigation-menu.tsx': shadcnComponents.navigationMenu,
  '/components/ui/pagination.tsx': shadcnComponents.pagination,
  '/components/ui/popover.tsx': shadcnComponents.popover,
  '/components/ui/progress.tsx': shadcnComponents.progress,
  '/components/ui/radio-group.tsx': shadcnComponents.radioGroup,
  '/components/ui/select.tsx': shadcnComponents.select,
  '/components/ui/separator.tsx': shadcnComponents.separator,
  '/components/ui/skeleton.tsx': shadcnComponents.skeleton,
  '/components/ui/slider.tsx': shadcnComponents.slider,
  '/components/ui/switch.tsx': shadcnComponents.switchComponent,
  '/components/ui/table.tsx': shadcnComponents.table,
  '/components/ui/tabs.tsx': shadcnComponents.tabs,
  '/components/ui/textarea.tsx': shadcnComponents.textarea,
  '/components/ui/toast.tsx': shadcnComponents.toast,
  '/components/ui/toaster.tsx': shadcnComponents.toaster,
  '/components/ui/toggle-group.tsx': shadcnComponents.toggleGroup,
  '/components/ui/toggle.tsx': shadcnComponents.toggle,
  '/components/ui/tooltip.tsx': shadcnComponents.tooltip,
  '/components/ui/use-toast.tsx': shadcnComponents.useToast,
  '/public/index.html': `<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Artifact</title>
    <script src="${TAILWIND_CDN}"></script>
    <style>
      ::-webkit-scrollbar{height:.1em;width:.5rem}
      ::-webkit-scrollbar-thumb{background-color:rgba(0,0,0,.1);border-radius:9999px}
      ::-webkit-scrollbar-track{background-color:transparent;border-radius:9999px}
      *{scrollbar-width:thin;scrollbar-color:rgba(0,0,0,.1) transparent}
    </style>
  </head>
  <body>
    <div id="root"></div>
  </body>
</html>`,
};

/**
 * URL self-hosted Sandpack bundler.
 * 1) REACT_APP_SANDPACK_BUNDLER_URL — явный URL
 * 2) REACT_APP_SANDPACK_BUNDLER_SAME_ORIGIN=true — /sandpack-bundler на том же origin (nginx → sidecar)
 */
export function getSandpackBundlerUrl(): string {
  const explicit = (process.env.REACT_APP_SANDPACK_BUNDLER_URL || '').trim();
  if (explicit) return explicit.replace(/\/$/, '');

  const sameOrigin = (process.env.REACT_APP_SANDPACK_BUNDLER_SAME_ORIGIN || '').trim().toLowerCase();
  if (sameOrigin === '1' || sameOrigin === 'true' || sameOrigin === 'yes') {
    if (typeof window !== 'undefined') {
      return `${window.location.origin}/sandpack-bundler`.replace(/\/$/, '');
    }
  }
  return '';
}

export function buildSandpackOptions(): SandpackProviderProps['options'] {
  const bundlerURL = getSandpackBundlerUrl();
  return {
    externalResources: [TAILWIND_CDN],
    ...(bundlerURL ? { bundlerURL } : {}),
    recompileMode: 'delayed',
    recompileDelay: 400,
  };
}

export function buildSandpackFiles(content: string): SandpackProviderProps['files'] {
  return {
    ...SANDPACK_SHARED_FILES,
    '/App.tsx': normalizeReactEntry(content),
    '/index.tsx': `import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App";

createRoot(document.getElementById("root")!).render(<App />);
`,
    '/styles.css': `html, body, #root { margin: 0; min-height: 100%; }
body { font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; }
`,
  };
}
