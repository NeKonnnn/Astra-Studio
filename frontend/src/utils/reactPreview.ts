/** Локальный React-preview в iframe (без Sandpack / CodeSandbox cloud bundler). */

const EXTERNAL_IMPORT_RE =
  /^import\s+[\s\S]*?\sfrom\s+['"](?!react(?:-dom)?(?:\/client)?['"])[^'"]+['"];?\s*$/gm;

export function normalizeReactEntry(raw: string): string {
  let code = (raw || '').trim();
  if (!code) {
    return `export default function Empty() { return React.createElement('div', null, 'Empty'); }`;
  }
  if (/^(tsx|jsx|typescript|javascript)\s*\n/i.test(code)) {
    code = code.replace(/^(tsx|jsx|typescript|javascript)\s*\n/i, '');
  }
  if (!/export\s+default/.test(code)) {
    const named = code.match(/(?:function|const)\s+([A-Z][A-Za-z0-9_]*)/);
    if (named) {
      code = `${code}\nexport default ${named[1]};`;
    } else {
      code = `export default function ArtifactApp() {\n  return (\n${code}\n  );\n}`;
    }
  }
  return code;
}

export function hasExternalReactImports(code: string): boolean {
  EXTERNAL_IMPORT_RE.lastIndex = 0;
  return EXTERNAL_IMPORT_RE.test(code);
}

/**
 * Экранирует код для вставки в JS template literal внутри inline <script>.
 * `</script` и `<!--` иначе преждевременно закрыли бы / исказили бы парсинг HTML-тега script.
 */
export function escapeForScriptTemplate(code: string): string {
  return code
    .replace(/\\/g, '\\\\')
    .replace(/`/g, '\\`')
    .replace(/\$\{/g, '\\${')
    .replace(/<\/(script)/gi, '<\\/$1')
    .replace(/<!--/g, '<\\!--');
}

/** Разбор `{ a, b as c, type T }` в элементы деструктуризации `a, b: c`. */
function namedImportsToDestructure(named: string): string[] {
  return named
    .split(',')
    .map((p) => p.trim())
    .filter((p) => p && !/^type\s/.test(p))
    .map((part) => {
      const [orig, alias] = part.split(/\s+as\s+/i).map((s) => s.trim());
      return alias && alias !== orig ? `${orig}: ${alias}` : orig;
    });
}

function prepareReactSource(code: string): string {
  let src = normalizeReactEntry(code);
  const namedReactImports: string[] = [];

  const reactAliases: string[] = [];

  // import React from 'react' | import React, { useState } from 'react' | import { useState } from 'react'
  // | import * as R from 'react' (+ вариант с `import type`)
  src = src.replace(
    /^import\s+(?:type\s+)?(?:\*\s+as\s+(\w+)|(\w+)\s*(?:,\s*\{([^}]*)\})?|\{([^}]*)\})\s+from\s+['"]react['"];?\s*$/gm,
    (
      _m,
      nsAlias: string | undefined,
      def: string | undefined,
      namedAfterDefault: string | undefined,
      namedOnly: string | undefined,
    ) => {
      const alias = nsAlias || def;
      // React уже передаётся в Function как параметр; другие алиасы привязываем к нему.
      if (alias && alias !== 'React') reactAliases.push(`const ${alias} = React;`);
      const named = namedAfterDefault ?? namedOnly;
      if (named) namedReactImports.push(...namedImportsToDestructure(named));
      return '';
    },
  );

  src = src.replace(/^import\s+[\s\S]*?\s+from\s+['"]react-dom(?:\/client)?['"];?\s*$/gm, '');
  src = src.replace(EXTERNAL_IMPORT_RE, '');

  const hookDecl = [
    ...reactAliases,
    namedReactImports.length > 0
      ? `const { ${Array.from(new Set(namedReactImports)).join(', ')} } = React;`
      : '',
  ]
    .filter(Boolean)
    .join('\n');

  const fnMatch = src.match(/export\s+default\s+function\s+([A-Za-z_$][\w$]*)/);
  if (fnMatch) {
    src = src.replace(/export\s+default\s+function\s+([A-Za-z_$][\w$]*)/, 'function $1');
    src = `${src}\nvar __App = ${fnMatch[1]};`;
  } else {
    src = src.replace(/export\s+default\s+/, 'var __App = ');
  }

  return `${hookDecl}\n${src}`.trim();
}

export function buildReactPreviewSrcDoc(rawCode: string): string {
  const publicBase = (process.env.PUBLIC_URL || '').replace(/\/$/, '');
  const prepared = prepareReactSource(rawCode);
  const escaped = escapeForScriptTemplate(prepared);

  return `<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <script crossorigin src="${publicBase}/static/react/react.production.min.js"></script>
  <script crossorigin src="${publicBase}/static/react/react-dom.production.min.js"></script>
  <script src="${publicBase}/static/babel.min.js"></script>
  <style>
    html, body { margin: 0; min-height: 100%; font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; background: #fff; }
    #root { min-height: 100vh; padding: 12px; box-sizing: border-box; }
    #err {
      display: none; margin: 12px; padding: 12px 14px; border-radius: 8px;
      background: #fee2e2; color: #991b1b; white-space: pre-wrap; font-size: 13px;
    }
  </style>
</head>
<body>
  <div id="root"></div>
  <div id="err"></div>
  <script>
    (function () {
      var errEl = document.getElementById('err');
      function showError(msg) {
        errEl.style.display = 'block';
        errEl.textContent = msg;
      }
      try {
        if (typeof React === 'undefined' || typeof ReactDOM === 'undefined' || typeof Babel === 'undefined') {
          showError('Не загружены React/Babel из /static/. Пересоберите фронтенд (prebuild).');
          return;
        }
        var source = \`${escaped}\`;
        var compiled = Babel.transform(source, {
          presets: ['react', 'typescript'],
          filename: 'Artifact.tsx',
        }).code;
        var module = { exports: {} };
        var exports = module.exports;
        var run = new Function('React', 'ReactDOM', 'exports', 'module', compiled);
        run(React, ReactDOM, exports, module);
        var App = module.exports.default || (typeof __App !== 'undefined' ? __App : null);
        if (!App) {
          showError('Не найден export default компонент.');
          return;
        }
        var root = ReactDOM.createRoot(document.getElementById('root'));
        root.render(React.createElement(App));
      } catch (e) {
        showError((e && e.message) ? e.message : String(e));
        console.error(e);
      }
    })();
  </script>
</body>
</html>`;
}
