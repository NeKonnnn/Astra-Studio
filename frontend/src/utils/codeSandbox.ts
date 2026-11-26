/**
 * Frontend-песочница для артефактов: JS / CSS / Python (Pyodide) в sandbox-iframe.
 * HTML и React живут в отдельных превью (ArtifactHtmlPreview / ArtifactReactPreview).
 */

export function escapeForScriptTemplate(code: string): string {
  return (code || '')
    .replace(/\\/g, '\\\\')
    .replace(/`/g, '\\`')
    .replace(/\$\{/g, '\\${')
    .replace(/<\/(script)/gi, '<\\/$1')
    .replace(/<!--/g, '<\\!--');
}

export function escapeHtmlText(s: string): string {
  return String(s || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/** Базовый URL Pyodide: env → локальный vendor → CDN. */
export function getPyodideIndexUrl(): string {
  const explicit = (process.env.REACT_APP_PYODIDE_INDEX_URL || '').trim();
  if (explicit) return explicit.replace(/\/?$/, '/');
  const publicBase = (process.env.PUBLIC_URL || '').replace(/\/$/, '');
  if (typeof window !== 'undefined') {
    // Предпочитаем self-hosted /vendor/pyodide/, если админ положил туда дистрибутив.
    return `${window.location.origin}${publicBase}/vendor/pyodide/`;
  }
  return `${publicBase}/vendor/pyodide/`;
}

const SANDBOX_CHROME = `
  :root { color-scheme: light; }
  * { box-sizing: border-box; }
  html, body { margin: 0; min-height: 100%; font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; background: #fff; color: #111827; }
  .layout { display: flex; flex-direction: column; min-height: 100vh; }
  .pane { padding: 12px 14px; }
  .preview { flex: 1; min-height: 140px; }
  .console {
    border-top: 1px solid #e5e7eb; background: #0f172a; color: #e2e8f0;
    font: 12px/1.5 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    white-space: pre-wrap; word-break: break-word; max-height: 40vh; overflow: auto;
  }
  .console .log { color: #e2e8f0; }
  .console .info { color: #93c5fd; }
  .console .warn { color: #fcd34d; }
  .console .error { color: #fca5a5; }
  .status { font-size: 12px; color: #6b7280; padding: 8px 14px; border-bottom: 1px solid #f3f4f6; }
  .demo-card {
    border: 1px dashed #cbd5e1; border-radius: 10px; padding: 16px; background: #f8fafc;
  }
`;

function consoleBootstrapScript(): string {
  return `
    (function () {
      var out = document.getElementById('console');
      function write(level, args) {
        var line = document.createElement('div');
        line.className = level;
        var text = Array.prototype.map.call(args, function (a) {
          try {
            if (typeof a === 'string') return a;
            if (a instanceof Error) return a.stack || a.message || String(a);
            return JSON.stringify(a, null, 2);
          } catch (e) { return String(a); }
        }).join(' ');
        line.textContent = text;
        out.appendChild(line);
        out.scrollTop = out.scrollHeight;
      }
      ['log', 'info', 'warn', 'error'].forEach(function (level) {
        var orig = console[level].bind(console);
        console[level] = function () {
          write(level, arguments);
          try { orig.apply(console, arguments); } catch (e) {}
        };
      });
      window.addEventListener('error', function (ev) {
        write('error', [ev.message || String(ev.error || 'Error')]);
      });
      window.addEventListener('unhandledrejection', function (ev) {
        write('error', [ev.reason || 'Unhandled rejection']);
      });
      window.__sandboxWrite = write;
    })();
  `;
}

/** Превью чистого JavaScript: DOM (#preview) + консоль. */
export function buildJsSandboxSrcDoc(rawCode: string): string {
  const code = escapeForScriptTemplate((rawCode || '').trim());
  return `<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <style>${SANDBOX_CHROME}</style>
</head>
<body>
  <div class="layout">
    <div class="status">JavaScript · песочница</div>
    <div id="preview" class="pane preview"></div>
    <div id="console" class="pane console"></div>
  </div>
  <script>${consoleBootstrapScript()}</script>
  <script>
    (function () {
      var source = \`${code}\`;
      try {
        var run = new Function('preview', source + '\\n;');
        run(document.getElementById('preview'));
      } catch (e) {
        window.__sandboxWrite('error', [(e && e.stack) || (e && e.message) || String(e)]);
      }
    })();
  </script>
</body>
</html>`;
}

/** Превью CSS на демо-разметке. */
export function buildCssSandboxSrcDoc(rawCss: string): string {
  const css = escapeHtmlText(rawCss || '');
  return `<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <style>${SANDBOX_CHROME}
  .demo-card h2 { margin: 0 0 8px; font-size: 1.15rem; }
  .demo-card p { margin: 0 0 12px; color: #475569; }
  .demo-card .btn {
    display: inline-block; padding: 8px 14px; border-radius: 8px;
    background: #2563eb; color: #fff; border: 0; cursor: pointer; font: inherit;
  }
  .demo-card .chip {
    display: inline-block; margin-right: 6px; padding: 4px 10px; border-radius: 999px;
    background: #e2e8f0; color: #0f172a; font-size: 12px;
  }
  </style>
  <style id="user-css">${css}</style>
</head>
<body>
  <div class="layout">
    <div class="status">CSS · демо-разметка ниже; стили применяются к ней</div>
    <div class="pane preview">
      <div class="demo-card" id="card">
        <h2 class="title">Заголовок карточки</h2>
        <p class="text">Параграф для проверки типографики и отступов.</p>
        <span class="chip">chip</span>
        <span class="chip accent">accent</span>
        <div style="margin-top:12px">
          <button class="btn" type="button">Кнопка</button>
        </div>
      </div>
    </div>
  </div>
</body>
</html>`;
}

/**
 * Python через Pyodide. Сначала пробуем indexURL (локальный /vendor или CDN из env),
 * при ошибке загрузки — понятное сообщение в консоли песочницы.
 */
export function buildPythonSandboxSrcDoc(rawCode: string): string {
  const code = escapeForScriptTemplate((rawCode || '').trim());
  const indexUrl = escapeForScriptTemplate(getPyodideIndexUrl());
  const cdnFallback = escapeForScriptTemplate(
    (process.env.REACT_APP_PYODIDE_CDN_URL || 'https://cdn.jsdelivr.net/pyodide/v0.26.4/full/').replace(
      /\/?$/,
      '/',
    ),
  );

  return `<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <style>${SANDBOX_CHROME}</style>
</head>
<body>
  <div class="layout">
    <div class="status" id="status">Python · загрузка интерпретатора…</div>
    <div id="preview" class="pane preview" style="display:none"></div>
    <div id="console" class="pane console"></div>
  </div>
  <script>${consoleBootstrapScript()}</script>
  <script>
    (function () {
      var statusEl = document.getElementById('status');
      var source = \`${code}\`;
      var primary = \`${indexUrl}\`;
      var fallback = \`${cdnFallback}\`;

      function loadScript(src) {
        return new Promise(function (resolve, reject) {
          var s = document.createElement('script');
          s.src = src;
          s.onload = function () { resolve(); };
          s.onerror = function () { reject(new Error('Не удалось загрузить ' + src)); };
          document.head.appendChild(s);
        });
      }

      async function loadPyodideFrom(indexURL) {
        await loadScript(indexURL + 'pyodide.js');
        if (typeof loadPyodide !== 'function') {
          throw new Error('loadPyodide не найден после загрузки скрипта');
        }
        return loadPyodide({ indexURL: indexURL });
      }

      async function main() {
        var pyodide = null;
        var lastErr = null;
        var urls = primary === fallback ? [primary] : [primary, fallback];
        for (var i = 0; i < urls.length; i++) {
          try {
            statusEl.textContent = 'Python · загрузка Pyodide (' + urls[i] + ')…';
            pyodide = await loadPyodideFrom(urls[i]);
            break;
          } catch (e) {
            lastErr = e;
            window.__sandboxWrite('warn', ['Pyodide: ' + ((e && e.message) || String(e))]);
          }
        }
        if (!pyodide) {
          statusEl.textContent = 'Python · интерпретатор недоступен';
          window.__sandboxWrite('error', [
            'Не удалось загрузить Pyodide. Положите дистрибутив в /vendor/pyodide/ или задайте REACT_APP_PYODIDE_INDEX_URL / REACT_APP_PYODIDE_CDN_URL. ' +
            ((lastErr && lastErr.message) || '')
          ]);
          return;
        }

        statusEl.textContent = 'Python · выполнение';
        pyodide.setStdout({
          batched: function (text) { window.__sandboxWrite('log', [text]); }
        });
        pyodide.setStderr({
          batched: function (text) { window.__sandboxWrite('error', [text]); }
        });

        try {
          await pyodide.runPythonAsync(source);
          statusEl.textContent = 'Python · готово';
        } catch (e) {
          statusEl.textContent = 'Python · ошибка';
          window.__sandboxWrite('error', [(e && e.message) || String(e)]);
        }
      }

      main();
    })();
  </script>
</body>
</html>`;
}
