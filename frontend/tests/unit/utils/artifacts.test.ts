import {
  artifactTypeLabel,
  extractArtifactsFromText,
  guessCodeLanguage,
  hoistPresentationArtifacts,
  isHtmlArtifactType,
  isMarkdownArtifactType,
  isMermaidArtifactType,
  isReactArtifactType,
  isSvgArtifactType,
  splitContentWithArtifacts,
  stripMermaidStyling,
} from '@/utils/artifacts';

const fenced = (attrs: string, lang: string, body: string, closed = true) =>
  `:::artifact{${attrs}}\n\`\`\`${lang}\n${body}\n${closed ? '```\n:::' : ''}`;

describe('type predicates', () => {
  it('recognize types case-insensitively', () => {
    expect(isHtmlArtifactType('TEXT/HTML')).toBe(true);
    expect(isHtmlArtifactType('application/vnd.code-html')).toBe(true);
    expect(isSvgArtifactType('image/svg+xml')).toBe(true);
    expect(isMarkdownArtifactType('text/md')).toBe(true);
    expect(isMermaidArtifactType('application/vnd.mermaid')).toBe(true);
    expect(isReactArtifactType('application/vnd.ant.react')).toBe(true);
  });

  it.each([null, undefined, '', 'text/plain'])('rejects %p everywhere', (type) => {
    const t = type as string;
    expect([
      isHtmlArtifactType(t),
      isSvgArtifactType(t),
      isMarkdownArtifactType(t),
      isMermaidArtifactType(t),
      isReactArtifactType(t),
    ]).toEqual([false, false, false, false, false]);
  });

  it('maps types to labels and code languages', () => {
    expect(artifactTypeLabel('text/html')).toBe('HTML');
    expect(artifactTypeLabel('image/svg+xml')).toBe('SVG');
    expect(artifactTypeLabel('application/vnd.mermaid')).toBe('Mermaid');
    expect(artifactTypeLabel('application/x-weird')).toBe('application/x-weird');
    expect(artifactTypeLabel('')).toBe('Artifact');
    expect(guessCodeLanguage('text/html')).toBe('html');
    expect(guessCodeLanguage('image/svg+xml')).toBe('xml');
    expect(guessCodeLanguage('text/markdown')).toBe('markdown');
    expect(guessCodeLanguage('application/vnd.react')).toBe('tsx');
    expect(guessCodeLanguage('whatever')).toBe('plaintext');
  });
});

describe('splitContentWithArtifacts', () => {
  it('returns a single empty text segment for empty input', () => {
    expect(splitContentWithArtifacts('')).toEqual([{ kind: 'text', text: '' }]);
  });

  it('returns plain text unchanged', () => {
    expect(splitContentWithArtifacts('Просто ответ без артефактов')).toEqual([
      { kind: 'text', text: 'Просто ответ без артефактов' },
    ]);
  });

  it('parses a fenced artifact between texts', () => {
    const text = `До\n${fenced('identifier="page" type="text/html" title="Страница"', 'html', '<p>hi</p>')}\nПосле`;

    const segments = splitContentWithArtifacts(text, { messageId: 'm1' });

    expect(segments.map((s) => s.kind)).toEqual(['text', 'artifact', 'text']);
    const artifact = (segments[1] as { artifact: ReturnType<typeof extractArtifactsFromText>[number] }).artifact;
    expect(artifact).toMatchObject({
      identifier: 'page',
      type: 'text/html',
      title: 'Страница',
      content: '<p>hi</p>',
      closed: true,
      messageId: 'm1',
    });
    expect(artifact.id).toContain('m1');
  });

  it('defaults identifier, title and type', () => {
    const [artifact] = extractArtifactsFromText(fenced('', '', 'просто текст'));

    expect(artifact.identifier).toBe('untitled');
    expect(artifact.title).toBe('untitled');
    expect(artifact.type).toBe('text/plain');
  });

  it('infers the type from the fence language', () => {
    const cases: Array<[string, string]> = [
      ['mermaid', 'application/vnd.mermaid'],
      ['html', 'text/html'],
      ['htm', 'text/html'],
      ['svg', 'image/svg+xml'],
      ['markdown', 'text/markdown'],
      ['md', 'text/markdown'],
      ['tsx', 'application/vnd.react'],
      ['javascript', 'application/vnd.react'],
    ];
    for (const [lang, expected] of cases) {
      const [artifact] = extractArtifactsFromText(fenced('identifier="x"', lang, 'body'));
      expect(artifact.type).toBe(expected);
    }
  });

  it('infers the type from the content when nothing is declared', () => {
    expect(extractArtifactsFromText(fenced('identifier="x"', '', 'graph TD\n  A --> B'))[0].type).toBe(
      'application/vnd.mermaid',
    );
    expect(extractArtifactsFromText(fenced('identifier="x"', '', '<svg viewBox="0 0 1 1"></svg>'))[0].type).toBe(
      'image/svg+xml',
    );
    expect(extractArtifactsFromText(fenced('identifier="x"', '', '<!DOCTYPE html><html></html>'))[0].type).toBe(
      'text/html',
    );
    expect(
      extractArtifactsFromText(fenced('identifier="x"', '', 'export default function App() { return null }'))[0].type,
    ).toBe('application/vnd.react');
  });

  it('trusts an explicit declared type over inference', () => {
    const [artifact] = extractArtifactsFromText(fenced('identifier="x" type="text/markdown"', 'html', '# h'));

    expect(artifact.type).toBe('text/markdown');
  });

  it('keeps a streaming artifact without a closing fence, marked as not closed', () => {
    const [artifact] = extractArtifactsFromText(fenced('identifier="x" type="text/html"', 'html', '<p>частично', false));

    expect(artifact.closed).toBe(false);
    expect(artifact.content).toContain('<p>частично');
  });

  it('parses an unfenced artifact (LibreChat format)', () => {
    const text = ':::artifact{identifier="u" type="text/html" title="U"}\n<p>тело</p>\n:::\nпосле';

    const segments = splitContentWithArtifacts(text);

    expect(segments.map((s) => s.kind)).toEqual(['artifact', 'text']);
    expect((segments[0] as { artifact: { content: string; closed: boolean } }).artifact).toMatchObject({
      content: '<p>тело</p>',
      closed: true,
    });
  });

  it('parses several artifacts in one message', () => {
    const text = `${fenced('identifier="a" type="text/html"', 'html', '<b>1</b>')}\nмежду\n${fenced(
      'identifier="b" type="image/svg+xml"',
      'svg',
      '<svg></svg>',
    )}`;

    expect(extractArtifactsFromText(text).map((a) => a.identifier)).toEqual(['a', 'b']);
  });

  it('accepts single-quoted attributes and a BOM / CRLF', () => {
    const text = `﻿:::artifact{identifier='q' title='T'}\r\n\`\`\`html\r\n<i>x</i>\r\n\`\`\`\r\n:::`;

    const [artifact] = extractArtifactsFromText(text);

    expect(artifact).toMatchObject({ identifier: 'q', title: 'T' });
  });

  it('does not treat ordinary code fences as artifacts', () => {
    expect(extractArtifactsFromText('```html\n<p>x</p>\n```')).toEqual([]);
  });

  it('keeps raw script content as data (never executes or strips it here)', () => {
    const [artifact] = extractArtifactsFromText(
      fenced('identifier="x" type="text/html"', 'html', '<script>alert(1)</script>'),
    );

    // Изоляцию обеспечивает песочница iframe, а не парсер: тело остаётся как есть.
    expect(artifact.content).toContain('<script>alert(1)</script>');
  });
});

describe('hoistPresentationArtifacts', () => {
  const art = (id: string, content: string) =>
    ({ kind: 'artifact', artifact: { id, content } }) as never;
  const text = (t: string) => ({ kind: 'text', text: t }) as never;
  const isPresentation = (content: string) => content.startsWith('PPT');

  it('returns short lists unchanged', () => {
    const segments = [art('a', 'x')];

    expect(hoistPresentationArtifacts(segments, isPresentation)).toBe(segments);
  });

  it('moves presentations first inside a run, keeping the order otherwise', () => {
    const result = hoistPresentationArtifacts(
      [art('a', 'x'), art('b', 'PPT1'), art('c', 'y'), art('d', 'PPT2')],
      isPresentation,
    ) as Array<{ artifact: { id: string } }>;

    expect(result.map((s) => s.artifact.id)).toEqual(['b', 'd', 'a', 'c']);
  });

  it('does not move artifacts across text segments', () => {
    const result = hoistPresentationArtifacts(
      [art('a', 'x'), text('между'), art('b', 'PPT1')],
      isPresentation,
    ) as Array<{ kind: string; artifact?: { id: string } }>;

    expect(result.map((s) => s.artifact?.id ?? 'text')).toEqual(['a', 'text', 'b']);
  });
});

describe('stripMermaidStyling', () => {
  it('removes style, classDef, class, linkStyle and click lines', () => {
    const code = [
      'graph TD',
      '  A --> B',
      '  style A fill:#f00',
      '  classDef big stroke:#000',
      '  class A big',
      '  linkStyle 0 stroke:red',
      '  click A callback',
    ].join('\n');

    expect(stripMermaidStyling(code)).toBe('graph TD\n  A --> B');
  });

  it('keeps ordinary edges that mention class-like words', () => {
    expect(stripMermaidStyling('graph TD\n  Classroom --> Student')).toBe('graph TD\n  Classroom --> Student');
  });
});
