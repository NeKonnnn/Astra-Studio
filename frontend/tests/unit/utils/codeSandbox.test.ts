import {
  buildCssSandboxSrcDoc,
  buildJsSandboxSrcDoc,
  buildPythonSandboxSrcDoc,
  escapeForScriptTemplate,
  escapeHtmlText,
  getPyodideCdnUrl,
  getPyodideIndexUrl,
  getPyodideLoadUrls,
  preparePythonSandboxSource,
} from '@/utils/codeSandbox';

describe('codeSandbox helpers', () => {
  const env = { ...process.env };
  afterEach(() => {
    process.env = { ...env };
  });

  it('escapeForScriptTemplate экранирует опасные последовательности', () => {
    expect(escapeForScriptTemplate('a`b${c}</script><!--')).toContain('\\`');
    expect(escapeForScriptTemplate('</script>')).toContain('<\\/script');
  });

  it('escapeHtmlText экранирует HTML', () => {
    expect(escapeHtmlText('<b>"&')).toBe('&lt;b&gt;&quot;&amp;');
  });

  it('getPyodideIndexUrl: явный env важнее vendor/CDN', () => {
    process.env.REACT_APP_PYODIDE_INDEX_URL = ' https://py.example/v1/ ';
    process.env.REACT_APP_PYODIDE_USE_VENDOR = 'true';
    expect(getPyodideIndexUrl()).toBe('https://py.example/v1/');
  });

  it('getPyodideIndexUrl: по умолчанию CDN, vendor только по флагу', () => {
    delete process.env.REACT_APP_PYODIDE_INDEX_URL;
    delete process.env.REACT_APP_PYODIDE_USE_VENDOR;
    expect(getPyodideIndexUrl()).toBe(getPyodideCdnUrl());
    process.env.REACT_APP_PYODIDE_USE_VENDOR = 'true';
    expect(getPyodideIndexUrl()).toContain('/vendor/pyodide/');
  });

  it('getPyodideLoadUrls: primary + fallback без дублей', () => {
    delete process.env.REACT_APP_PYODIDE_INDEX_URL;
    delete process.env.REACT_APP_PYODIDE_USE_VENDOR;
    const urls = getPyodideLoadUrls();
    expect(urls[0]).toBe(getPyodideCdnUrl());
    expect(urls[1]).toContain('/vendor/pyodide/');
    expect(new Set(urls).size).toBe(urls.length);
  });

  it('buildJsSandboxSrcDoc содержит код и консоль', () => {
    const doc = buildJsSandboxSrcDoc('console.log(1)');
    expect(doc).toContain('console.log(1)');
    expect(doc).toContain('id="console"');
    expect(doc).toContain('id="preview"');
  });

  it('buildCssSandboxSrcDoc вставляет CSS пользователя', () => {
    const doc = buildCssSandboxSrcDoc('.btn{color:red}');
    expect(doc).toContain('.btn{color:red}');
    expect(doc).toContain('id="user-css"');
    expect(doc).toContain('demo-card');
  });

  it('buildPythonSandboxSrcDoc грузит micropip и исходник', () => {
    process.env.REACT_APP_PYODIDE_INDEX_URL = 'https://py.local/';
    const doc = buildPythonSandboxSrcDoc('import micropip\\nprint(42)');
    expect(doc).toContain('print(42)');
    expect(doc).toContain('https://py.local/');
    expect(doc).toContain('loadPyodide');
    expect(doc).toContain("loadPackage('micropip')");
    expect(doc).toContain('loadPackagesFromImports');
  });

  it('buildPythonSandboxSrcDoc для pygame привязывает canvas', () => {
    const doc = buildPythonSandboxSrcDoc('import pygame\\npygame.init()');
    expect(doc).toContain('id="canvas"');
    expect(doc).toContain('setCanvas2D');
    expect(doc).toContain('wantsPygame = true');
  });

  it('preparePythonSandboxSource переводит pygame while/tick в async', () => {
    const src = [
      'import pygame',
      'def main():',
      '    running = True',
      '    clock = pygame.time.Clock()',
      '    while running:',
      '        pygame.display.flip()',
      '        clock.tick(60)',
      'main()',
    ].join('\n');
    const { code, pygamePatched } = preparePythonSandboxSource(src);
    expect(pygamePatched).toBe(true);
    expect(code).toContain('async def main(');
    expect(code).toContain('await asyncio.sleep(0)');
    expect(code).toContain('await main()');
  });

  it('preparePythonSandboxSource делает async любой def с await (не только main)', () => {
    const src = [
      'import pygame',
      'def play():',
      '    running = True',
      '    while running:',
      '        for e in pygame.event.get():',
      '            pass',
      '        pygame.display.flip()',
      'play()',
    ].join('\n');
    const { code, pygamePatched } = preparePythonSandboxSource(src);
    expect(pygamePatched).toBe(true);
    expect(code).toContain('async def play(');
    expect(code).toMatch(/await\s+play\s*\(\s*\)/);
    expect(code).not.toMatch(/(?:^|\n)def\s+play\s*\(/);
  });
});
