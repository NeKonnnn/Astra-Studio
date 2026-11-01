import React from 'react';
import { render, screen } from '@testing-library/react';
import '@testing-library/jest-dom';
import ArtifactReactPreview from '@/components/artifacts/ArtifactReactPreview';

const providerProps: any[] = [];
const previewProps: any[] = [];

jest.mock('@codesandbox/sandpack-react', () => ({
  SandpackProvider: (p: any) => {
    providerProps.push(p);
    return <div data-testid="sandpack-provider">{p.children}</div>;
  },
  SandpackPreview: (p: any) => {
    previewProps.push(p);
    return <div data-testid="sandpack-preview" />;
  },
}));

function lastFiles(): Record<string, string> {
  return providerProps[providerProps.length - 1].files;
}

describe('ArtifactReactPreview', () => {
  beforeEach(() => {
    providerProps.length = 0;
    previewProps.length = 0;
  });

  it('во время стрима Sandpack не монтируется', () => {
    render(<ArtifactReactPreview content="export default () => null" isStreaming />);
    expect(screen.queryByTestId('sandpack-provider')).toBeNull();
    expect(providerProps).toHaveLength(0);
  });

  it('пустой/пробельный код — заглушка вместо Sandpack', () => {
    const { rerender } = render(<ArtifactReactPreview content="" />);
    expect(screen.queryByTestId('sandpack-provider')).toBeNull();
    rerender(<ArtifactReactPreview content={'  \n '} />);
    expect(screen.queryByTestId('sandpack-provider')).toBeNull();
    rerender(<ArtifactReactPreview content={undefined as unknown as string} />);
    expect(screen.queryByTestId('sandpack-provider')).toBeNull();
  });

  it('код с export default передаётся в /App.tsx без изменений (trim)', () => {
    const code = 'export default function App() { return <b>hi</b>; }';
    render(<ArtifactReactPreview content={`\n${code}\n`} />);
    expect(screen.getByTestId('sandpack-provider')).toBeInTheDocument();
    expect(lastFiles()['/App.tsx']).toBe(code);
  });

  it('конфигурирует react-ts шаблон, Tailwind CDN, зависимости и кнопку обновления', () => {
    render(<ArtifactReactPreview content="export default 1" />);
    const p = providerProps[0];
    expect(p.template).toBe('react-ts');
    expect(p.theme).toBe('light');
    expect(p.options.externalResources).toEqual(['https://cdn.tailwindcss.com']);
    expect(p.options.recompileMode).toBe('delayed');
    expect(Object.keys(p.customSetup.dependencies)).toEqual(
      expect.arrayContaining(['recharts', 'lucide-react', 'clsx']),
    );
    expect(previewProps[0].showOpenInCodeSandbox).toBe(false);
    expect(previewProps[0].showRefreshButton).toBe(true);
    const files = lastFiles();
    expect(files['/index.tsx']).toContain('createRoot(document.getElementById("root")!)');
    expect(files['/styles.css']).toContain('#root');
    expect(files['/public/index.html']).toContain('<div id="root"></div>');
  });

  it('без export default экспортирует первый именованный компонент (function)', () => {
    render(<ArtifactReactPreview content={'function Counter() { return <i/>; }'} />);
    expect(lastFiles()['/App.tsx']).toBe(
      'function Counter() { return <i/>; }\nexport default Counter;',
    );
  });

  it('без export default экспортирует именованный компонент, объявленный через const', () => {
    render(<ArtifactReactPreview content={'const Widget = () => <u/>;'} />);
    expect(lastFiles()['/App.tsx']).toContain('export default Widget;');
  });

  it('голый JSX без компонента оборачивается в ArtifactApp', () => {
    render(<ArtifactReactPreview content={'<div>просто разметка</div>'} />);
    expect(lastFiles()['/App.tsx']).toBe(
      'export default function ArtifactApp() {\n  return (\n<div>просто разметка</div>\n  );\n}',
    );
  });

  it('удаляет оставшийся от fence язык (tsx/jsx/typescript/javascript) в первой строке', () => {
    render(<ArtifactReactPreview content={'tsx\nexport default function A(){return null}'} />);
    expect(lastFiles()['/App.tsx']).toBe('export default function A(){return null}');
  });

  it('префикс-язык без перевода строки не трогается', () => {
    render(<ArtifactReactPreview content={'jsxFoo export default 1'} />);
    expect(lastFiles()['/App.tsx']).toBe('jsxFoo export default 1');
  });

  it('после стрима превью получает финальный код', () => {
    const { rerender } = render(<ArtifactReactPreview content="export default 1" isStreaming />);
    expect(screen.queryByTestId('sandpack-provider')).toBeNull();
    rerender(<ArtifactReactPreview content="export default 2" isStreaming={false} />);
    expect(lastFiles()['/App.tsx']).toBe('export default 2');
  });
});
