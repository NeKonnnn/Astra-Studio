import {
  REACT_ARTIFACT_DEPENDENCIES,
  SANDPACK_SHARED_FILES,
  buildSandpackFiles,
  buildSandpackOptions,
  getSandpackBundlerUrl,
} from '@/utils/sandpackArtifacts';
import * as shadcn from '@/utils/sandpackShadcn.generated';

describe('sandpackArtifacts', () => {
  const env = { ...process.env };
  afterEach(() => {
    process.env = { ...env };
  });

  it('getSandpackBundlerUrl: явный URL без хвостового слэша приоритетнее', () => {
    process.env.REACT_APP_SANDPACK_BUNDLER_URL = ' https://b.example.com/ ';
    process.env.REACT_APP_SANDPACK_BUNDLER_SAME_ORIGIN = 'true';
    expect(getSandpackBundlerUrl()).toBe('https://b.example.com');
  });

  it('getSandpackBundlerUrl: same-origin флаг (1/true/yes) даёт путь на текущем origin', () => {
    delete process.env.REACT_APP_SANDPACK_BUNDLER_URL;
    for (const v of ['1', 'TRUE', 'yes']) {
      process.env.REACT_APP_SANDPACK_BUNDLER_SAME_ORIGIN = v;
      expect(getSandpackBundlerUrl()).toBe(`${window.location.origin}/sandpack-bundler`);
    }
  });

  it('getSandpackBundlerUrl: по умолчанию пусто', () => {
    delete process.env.REACT_APP_SANDPACK_BUNDLER_URL;
    process.env.REACT_APP_SANDPACK_BUNDLER_SAME_ORIGIN = 'no';
    expect(getSandpackBundlerUrl()).toBe('');
    delete process.env.REACT_APP_SANDPACK_BUNDLER_SAME_ORIGIN;
    expect(getSandpackBundlerUrl()).toBe('');
  });

  it('buildSandpackOptions: bundlerURL добавляется только когда задан', () => {
    delete process.env.REACT_APP_SANDPACK_BUNDLER_URL;
    delete process.env.REACT_APP_SANDPACK_BUNDLER_SAME_ORIGIN;
    const base = buildSandpackOptions() as any;
    expect(base.bundlerURL).toBeUndefined();
    expect(base.recompileMode).toBe('delayed');
    expect(base.recompileDelay).toBe(400);
    expect(base.externalResources).toEqual(['https://cdn.tailwindcss.com/3.4.17#tailwind.js']);
    process.env.REACT_APP_SANDPACK_BUNDLER_URL = 'http://x/';
    expect((buildSandpackOptions() as any).bundlerURL).toBe('http://x');
  });

  it('buildSandpackFiles: подставляет нормализованный App.tsx, entry и стили', () => {
    const files = buildSandpackFiles('function Hello() { return null }') as Record<string, string>;
    expect(files['/App.tsx']).toContain('export default Hello;');
    expect(files['/index.tsx']).toContain('createRoot(document.getElementById("root")!)');
    expect(files['/styles.css']).toContain('#root');
    expect(files['/lib/utils.ts']).toBe(shadcn.utils);
    expect(files['/components/ui/button.tsx']).toBe(shadcn.button);
    expect(files['/public/index.html']).toContain('cdn.tailwindcss.com');
    expect((buildSandpackFiles('') as Record<string, string>)['/App.tsx']).toContain('Empty');
  });

  it('общие файлы покрывают shadcn-компоненты из генератора', () => {
    const shared = SANDPACK_SHARED_FILES as Record<string, string>;
    const ui = Object.keys(shared).filter((k) => k.startsWith('/components/ui/'));
    expect(ui.length).toBeGreaterThan(30);
    for (const k of ui) expect(typeof shared[k]).toBe('string');
    for (const src of Object.values(shadcn.shadcnComponents)) {
      expect(typeof src).toBe('string');
      expect((src as string).length).toBeGreaterThan(10);
    }
    // hoverCard сгенерирован, но в SANDPACK_SHARED_FILES не подключён
    expect(Object.values(shared)).not.toContain(shadcn.hoverCard);
  });

  it('зависимости содержат radix и three', () => {
    expect(REACT_ARTIFACT_DEPENDENCIES.three).toBeDefined();
    expect(REACT_ARTIFACT_DEPENDENCIES['@radix-ui/react-dialog']).toBeDefined();
  });
});
