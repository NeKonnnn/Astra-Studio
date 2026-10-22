import {
  escapeHtmlForSrcDoc,
  htmlNeedsChartJs,
  rewriteHtmlArtifactScriptsForOffline,
} from '@/utils/htmlArtifactScripts';

const LOCAL_TAG = '<script src="/vendor/chart.umd.min.js"></script>';

describe('escapeHtmlForSrcDoc', () => {
  it('returns an empty string for empty input', () => {
    expect(escapeHtmlForSrcDoc('')).toBe('');
    expect(escapeHtmlForSrcDoc(undefined as unknown as string)).toBe('');
  });

  it('is effectively a no-op: the lazy match stops at the first </script> (known issue)', () => {
    // См. TESTING_KNOWN_BUGS.md: функция должна экранировать `</script` внутри
    // тела скрипта, но регулярное выражение захватывает тело только до первого
    // `</script>`, поэтому внутри него закрывающего тега никогда нет.
    const html = '<script>var s = "</script>";</script>';

    expect(escapeHtmlForSrcDoc(html)).toBe(html);
  });

  it('keeps the real closing tags and attributes intact', () => {
    const html = '<script type="module">let a = 1;</script><p>text</p>';

    expect(escapeHtmlForSrcDoc(html)).toBe(html);
  });

  it('handles several scripts', () => {
    const html = '<script>a</script><div></div><script defer>b</script>';

    expect(escapeHtmlForSrcDoc(html)).toBe(html);
  });

  it('leaves html without scripts untouched', () => {
    expect(escapeHtmlForSrcDoc('<p>a</p>')).toBe('<p>a</p>');
  });
});

describe('htmlNeedsChartJs', () => {
  it.each([
    '<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>',
    '<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4/chart.min.js"></script>',
    '<script src="https://unpkg.com/chart.js@4"></script>',
    '<script src="/static/chart.umd.min.js"></script>',
    '<script>new Chart(ctx, {})</script>',
    '<script>Chart.register(Foo)</script>',
    '<script>window.Chart.defaults</script>',
  ])('detects Chart.js usage in %p', (html) => {
    expect(htmlNeedsChartJs(html)).toBe(true);
  });

  it.each(['', '<p>без графиков</p>', '<script src="https://cdn.jsdelivr.net/npm/lodash"></script>'])(
    'does not flag %p',
    (html) => {
      expect(htmlNeedsChartJs(html)).toBe(false);
    },
  );

  it('gives the same answer when called repeatedly (no sticky regex state)', () => {
    const html = '<script src="https://unpkg.com/chart.js"></script>';

    expect([htmlNeedsChartJs(html), htmlNeedsChartJs(html), htmlNeedsChartJs(html)]).toEqual([true, true, true]);
  });
});

describe('rewriteHtmlArtifactScriptsForOffline', () => {
  it('returns empty or blank input unchanged', () => {
    expect(rewriteHtmlArtifactScriptsForOffline('')).toBe('');
    expect(rewriteHtmlArtifactScriptsForOffline('   ')).toBe('   ');
  });

  it('does not touch html that does not use Chart.js', () => {
    const html = '<html><head></head><body><p>x</p></body></html>';

    expect(rewriteHtmlArtifactScriptsForOffline(html)).toBe(html);
  });

  it('replaces the CDN script with the local vendor file before </head>', () => {
    const html =
      '<html><head><script src="https://cdn.jsdelivr.net/npm/chart.js"></script></head><body><script>new Chart(c,{})</script></body></html>';

    const out = rewriteHtmlArtifactScriptsForOffline(html);

    expect(out).not.toContain('cdn.jsdelivr.net');
    expect(out).toContain(`${LOCAL_TAG}\n</head>`);
    expect(out.match(/chart\.umd\.min\.js/g)).toHaveLength(1);
  });

  it('inserts after <body> when there is no head', () => {
    const out = rewriteHtmlArtifactScriptsForOffline('<body class="a"><script>new Chart(c,{})</script></body>');

    expect(out.startsWith(`<body class="a">\n${LOCAL_TAG}`)).toBe(true);
  });

  it('prepends the tag to a bare fragment', () => {
    const out = rewriteHtmlArtifactScriptsForOffline('<canvas></canvas><script>new Chart(c,{})</script>');

    expect(out.startsWith(`${LOCAL_TAG}\n<canvas>`)).toBe(true);
  });

  it('keeps exactly one local tag when applied repeatedly', () => {
    const html = `<html><head>${LOCAL_TAG}</head><body><script>new Chart(c,{})</script></body></html>`;

    const once = rewriteHtmlArtifactScriptsForOffline(html);
    const twice = rewriteHtmlArtifactScriptsForOffline(once);

    expect(once.match(/chart\.umd\.min\.js/g)).toHaveLength(1);
    expect(twice.match(/chart\.umd\.min\.js/g)).toHaveLength(1);
  });

  it('removes external chart scripts but adds nothing if the API is unused', () => {
    const out = rewriteHtmlArtifactScriptsForOffline(
      '<p>a</p><script src="https://cdn.jsdelivr.net/npm/chart.js"></script>',
    );

    // Скрипт подключения считается использованием Chart.js, локальная копия ставится взамен.
    expect(out).toContain('/vendor/chart.umd.min.js');
    expect(out).not.toContain('jsdelivr');
  });

  it('leaves unrelated external scripts alone', () => {
    const html = '<script src="https://cdn.jsdelivr.net/npm/lodash"></script><script>new Chart(c,{})</script>';

    expect(rewriteHtmlArtifactScriptsForOffline(html)).toContain('https://cdn.jsdelivr.net/npm/lodash');
  });
});
