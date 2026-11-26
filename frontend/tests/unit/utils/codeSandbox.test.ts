import {
  buildCssSandboxSrcDoc,
  buildJsSandboxSrcDoc,
  buildPythonSandboxSrcDoc,
  escapeForScriptTemplate,
  escapeHtmlText,
  getPyodideIndexUrl,
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

  it('getPyodideIndexUrl: явный env важнее vendor', () => {
    process.env.REACT_APP_PYODIDE_INDEX_URL = ' https://py.example/v1/ ';
    expect(getPyodideIndexUrl()).toBe('https://py.example/v1/');
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

  it('buildPythonSandboxSrcDoc подключает Pyodide и исходник', () => {
    process.env.REACT_APP_PYODIDE_INDEX_URL = 'https://py.local/';
    const doc = buildPythonSandboxSrcDoc('print(42)');
    expect(doc).toContain('print(42)');
    expect(doc).toContain('https://py.local/');
    expect(doc).toContain('loadPyodide');
  });
});
