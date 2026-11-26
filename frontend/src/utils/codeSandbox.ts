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

const DEFAULT_PYODIDE_CDN = 'https://cdn.jsdelivr.net/pyodide/v0.26.4/full/';

function withTrailingSlash(url: string): string {
  return url.replace(/\/?$/, '/');
}

function vendorPyodideUrl(): string {
  const publicBase = (process.env.PUBLIC_URL || '').replace(/\/$/, '');
  if (typeof window !== 'undefined') {
    return `${window.location.origin}${publicBase}/vendor/pyodide/`;
  }
  return `${publicBase}/vendor/pyodide/`;
}

/** CDN Pyodide (или REACT_APP_PYODIDE_CDN_URL). */
export function getPyodideCdnUrl(): string {
  const cdn = (process.env.REACT_APP_PYODIDE_CDN_URL || '').trim();
  return withTrailingSlash(cdn || DEFAULT_PYODIDE_CDN);
}

/**
 * Основной URL Pyodide:
 * 1) REACT_APP_PYODIDE_INDEX_URL — явный
 * 2) REACT_APP_PYODIDE_USE_VENDOR=true — /vendor/pyodide/ (офлайн / корп. сеть)
 * 3) иначе CDN (локальный vendor по умолчанию пустой → без лишнего 404)
 */
export function getPyodideIndexUrl(): string {
  const explicit = (process.env.REACT_APP_PYODIDE_INDEX_URL || '').trim();
  if (explicit) return withTrailingSlash(explicit);
  const useVendor = (process.env.REACT_APP_PYODIDE_USE_VENDOR || '').trim().toLowerCase();
  if (useVendor === '1' || useVendor === 'true' || useVendor === 'yes') {
    return vendorPyodideUrl();
  }
  return getPyodideCdnUrl();
}

/** Порядок попыток загрузки: primary + fallback (vendor↔CDN). */
export function getPyodideLoadUrls(): string[] {
  const primary = getPyodideIndexUrl();
  const vendor = vendorPyodideUrl();
  const cdn = getPyodideCdnUrl();
  const fallback = primary === cdn ? vendor : cdn;
  if (primary === fallback) return [primary];
  return [primary, fallback];
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

export function codeLooksLikePygame(code: string): boolean {
  return /\b(?:pygame(?:_ce)?|from\s+pygame)\b/i.test(code || '');
}

/**
 * Любой def, в теле которого есть await, → async def;
 * голые вызовы name() этих функций → await name().
 */
function promoteFunctionsContainingAwait(code: string): string {
  const lines = code.split('\n');
  type Frame = { indent: number; lineIdx: number; name: string; isAsync: boolean };
  const stack: Frame[] = [];
  const promoteIdx = new Set<number>();
  const promoteNames = new Set<string>();

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const indent = (line.match(/^(\s*)/) || ['', ''])[1].length;

    while (stack.length && indent <= stack[stack.length - 1].indent) {
      stack.pop();
    }

    const defMatch = line.match(/^(\s*)(async\s+)?def\s+([A-Za-z_]\w*)\s*\(/);
    if (defMatch) {
      stack.push({
        indent: defMatch[1].length,
        lineIdx: i,
        name: defMatch[3],
        isAsync: Boolean(defMatch[2]),
      });
      continue;
    }

    if (/\bawait\b/.test(line) && stack.length) {
      const top = stack[stack.length - 1];
      if (!top.isAsync) {
        promoteIdx.add(top.lineIdx);
        promoteNames.add(top.name);
        top.isAsync = true;
      }
    }
  }

  Array.from(promoteIdx).forEach((idx) => {
    lines[idx] = lines[idx].replace(/^(\s*)def\s+/, '$1async def ');
  });

  let out = lines.join('\n');
  Array.from(promoteNames).forEach((name) => {
    const re = new RegExp(`^(\\s*)${name}\\s*\\(\\s*\\)\\s*$`, 'gm');
    out = out.replace(re, `$1await ${name}()`);
  });
  return out;
}

/**
 * Синхронный while True у pygame блокирует вкладку в Pyodide.
 * Приводим типичный LLM-код к async + await asyncio.sleep(0).
 */
export function preparePythonSandboxSource(raw: string): { code: string; pygamePatched: boolean } {
  let code = (raw || '').replace(/\r\n/g, '\n').trim();
  if (!code || !codeLooksLikePygame(code)) {
    return { code, pygamePatched: false };
  }

  const alreadyAsync =
    /await\s+asyncio\.sleep\s*\(/.test(code) && /async\s+def\s+/.test(code);

  // После flip/tick отдаём кадр браузеру
  const lines = code.split('\n');
  const out: string[] = [];
  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];
    out.push(line);
    if (/pygame\.display\.(?:flip|update)\s*\(/.test(line) || /\.tick\s*\(\s*\d*\s*\)/.test(line)) {
      const next = lines[i + 1] || '';
      if (/await\s+asyncio\.sleep\s*\(/.test(next)) continue;
      const indent = (line.match(/^(\s*)/) || ['', ''])[1];
      out.push(`${indent}await asyncio.sleep(0)`);
    }
  }
  code = out.join('\n');

  // В начале тела while — yield в event loop (не дублируем, если уже есть)
  code = code.replace(
    /^(while\s+[^:\n]+:)(\n)([ \t]+)(?!await\s+asyncio\.sleep\s*\()/gm,
    (_m, w: string, nl: string, indent: string) =>
      `${w}${nl}${indent}await asyncio.sleep(0)${nl}${indent}`
  );

  // Важно: await мог попасть в обычный def (не main) → SyntaxError
  code = promoteFunctionsContainingAwait(code);

  code = code.replace(/\basyncio\.run\s*\(\s*([^)]+?)\s*\)/g, 'await $1');

  if (!/^import\s+asyncio\b/m.test(code)) {
    code = `import asyncio\n${code}`;
  }

  // Точки входа без вызова
  if (/async\s+def\s+main\s*\(/.test(code) && !/await\s+main\s*\(/.test(code)) {
    code = `${code.replace(/\s+$/, '')}\n\nawait main()\n`;
  } else if (
    /async\s+def\s+(run|game_loop|play|start)\s*\(/.test(code) &&
    !/await\s+(?:run|game_loop|play|start)\s*\(/.test(code) &&
    !/await\s+main\s*\(/.test(code)
  ) {
    const m = code.match(/async\s+def\s+(run|game_loop|play|start)\s*\(/);
    if (m) {
      code = `${code.replace(/\s+$/, '')}\n\nawait ${m[1]}()\n`;
    }
  }

  // Плоский скрипт: await на модуле ок для runPythonAsync, но если нет async def —
  // и await только внутри несуществующих scopes не бывает; если всё ещё нет async —
  // оборачиваем целиком.
  if (!/async\s+def\s+/.test(code) && /\bawait\b/.test(code)) {
    const indented = code
      .split('\n')
      .map((l) => (l.length ? `    ${l}` : l))
      .join('\n');
    code = `import asyncio\nasync def __astra_pygame_main():\n${indented}\n\nawait __astra_pygame_main()\n`;
  }

  return { code, pygamePatched: !alreadyAsync };
}

/**
 * Python через Pyodide.
 * После старта: micropip + loadPackagesFromImports(code), canvas для pygame, runPythonAsync.
 */
export function buildPythonSandboxSrcDoc(rawCode: string): string {
  const prepared = preparePythonSandboxSource(rawCode || '');
  const code = escapeForScriptTemplate(prepared.code);
  const urlsJson = JSON.stringify(getPyodideLoadUrls());
  const wantsPygame = codeLooksLikePygame(rawCode || '');
  const pygamePatched = prepared.pygamePatched;

  return `<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <style>${SANDBOX_CHROME}
  #preview { background: #0b1220; display: flex; align-items: center; justify-content: center; }
  #canvas {
    display: block; max-width: 100%; background: #000;
    image-rendering: pixelated; outline: none;
  }
  </style>
</head>
<body>
  <div class="layout">
    <div class="status" id="status">Python · загрузка интерпретатора…</div>
    <div id="preview" class="pane preview" style="display:${wantsPygame ? 'flex' : 'none'}">
      <canvas id="canvas" width="640" height="480" tabindex="0"></canvas>
    </div>
    <div id="console" class="pane console"></div>
  </div>
  <script>${consoleBootstrapScript()}</script>
  <script>
    (function () {
      var statusEl = document.getElementById('status');
      var previewEl = document.getElementById('preview');
      var canvas = document.getElementById('canvas');
      var source = \`${code}\`;
      var urls = ${urlsJson};
      var wantsPygame = ${wantsPygame ? 'true' : 'false'};
      var pygamePatched = ${pygamePatched ? 'true' : 'false'};

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
        try { delete window.loadPyodide; } catch (e) { window.loadPyodide = undefined; }
        await loadScript(indexURL + 'pyodide.js');
        if (typeof loadPyodide !== 'function') {
          throw new Error('loadPyodide не найден после загрузки скрипта');
        }
        return loadPyodide({ indexURL: indexURL });
      }

      function bindPygameCanvas(pyodide) {
        if (!wantsPygame) return;
        previewEl.style.display = 'flex';
        try { canvas.focus(); } catch (e) {}
        // Pyodide 0.24+: без setCanvas2D pygame падает на createImageData(undefined)
        if (pyodide.canvas && typeof pyodide.canvas.setCanvas2D === 'function') {
          pyodide.canvas.setCanvas2D(canvas);
          window.__sandboxWrite('info', ['pygame: canvas привязан (' + canvas.width + 'x' + canvas.height + ')']);
        } else {
          // Fallback для старых сборок / emscripten Module.canvas
          window.Module = window.Module || {};
          window.Module.canvas = canvas;
          window.__sandboxWrite('warn', [
            'pygame: pyodide.canvas.setCanvas2D недоступен — используется Module.canvas'
          ]);
        }
      }

      async function ensurePackages(pyodide) {
        statusEl.textContent = 'Python · пакеты (micropip)…';
        try {
          await pyodide.loadPackage('micropip');
        } catch (e) {
          window.__sandboxWrite('warn', [
            'micropip: ' + ((e && e.message) || String(e))
          ]);
        }
        if (typeof pyodide.loadPackagesFromImports === 'function') {
          statusEl.textContent = 'Python · пакеты из import…';
          try {
            await pyodide.loadPackagesFromImports(source);
          } catch (e) {
            window.__sandboxWrite('warn', [
              'loadPackagesFromImports: ' + ((e && e.message) || String(e))
            ]);
          }
        }
      }

      async function main() {
        var pyodide = null;
        var lastErr = null;
        for (var i = 0; i < urls.length; i++) {
          try {
            statusEl.textContent = 'Python · загрузка Pyodide…';
            pyodide = await loadPyodideFrom(urls[i]);
            window.__sandboxWrite('info', ['Pyodide: ' + urls[i]]);
            break;
          } catch (e) {
            lastErr = e;
            window.__sandboxWrite('warn', ['Pyodide: ' + ((e && e.message) || String(e))]);
          }
        }
        if (!pyodide) {
          statusEl.textContent = 'Python · интерпретатор недоступен';
          window.__sandboxWrite('error', [
            'Не удалось загрузить Pyodide. Нужен доступ к CDN или файлы в /vendor/pyodide/ ' +
            '(REACT_APP_PYODIDE_USE_VENDOR=true / REACT_APP_PYODIDE_INDEX_URL). ' +
            ((lastErr && lastErr.message) || '')
          ]);
          return;
        }

        pyodide.setStdout({
          batched: function (text) { window.__sandboxWrite('log', [text]); }
        });
        pyodide.setStderr({
          batched: function (text) { window.__sandboxWrite('error', [text]); }
        });

        try {
          await ensurePackages(pyodide);
          bindPygameCanvas(pyodide);
          if (pygamePatched) {
            window.__sandboxWrite('info', [
              'pygame: код обёрнут в async + await asyncio.sleep(0), иначе while-цикл зависает во вкладке'
            ]);
          }
          statusEl.textContent = wantsPygame ? 'Python · игра…' : 'Python · выполнение…';
          await pyodide.runPythonAsync(source);
          statusEl.textContent = 'Python · готово';
        } catch (e) {
          statusEl.textContent = 'Python · ошибка';
          var msg = (e && e.message) || String(e);
          if (/createImageData|canvas/i.test(msg)) {
            msg += '\\n\\nPygame в песочнице нужен canvas (уже есть #canvas). ' +
              'Убедитесь, что set_mode вызывается после import pygame, ' +
              'и цикл не блокирует поток без await asyncio.sleep(0).';
          }
          window.__sandboxWrite('error', [msg]);
        }
      }

      main();
    })();
  </script>
</body>
</html>`;
}
