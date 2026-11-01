import {
  SKILLS_CHANGED_EVENT,
  exportSkill,
  exportSkillsJson,
  formToPortableSkill,
  formatSkillsApiDetail,
  importSkillFile,
  parseSkillFrontmatter,
  portableSkillToFormFields,
  serializeSkillToMd,
  slugifySkillName,
  type PortableSkill,
  type SkillFormLike,
} from '@/utils/skillsImportExport';

jest.mock('@/config/api', () => ({
  authFetch: (...a: unknown[]) => (require('@tests/unit/helpers/authFetchMock').authFetchMock as (...x: unknown[]) => unknown)(...a),
  API_ENDPOINTS: { SKILLS: '/skills' },
  getApiUrl: (p: string) => `http://api.test${p}`,
}));

const form = (over: Partial<SkillFormLike> = {}): SkillFormLike => ({
  slug: '',
  name: 'Мой навык',
  display_title: '',
  description: ' описание ',
  content: 'тело',
  is_active: true,
  is_public: false,
  user_invocable: true,
  disable_model_invocation: false,
  always_apply: false,
  allowed_tools: 'a, b;c\nd',
  category: ' cat ',
  tags: 't1, t2',
  ...over,
});

function fileOf(name: string, text: string): File {
  const f: any = new File([text], name);
  f.text = async () => text;
  return f as File;
}

describe('skillsImportExport: прочие функции', () => {
  let blobs: Blob[];
  let names: string[];
  let fetchMock: jest.Mock;

  beforeEach(() => {
    blobs = [];
    names = [];
    (URL as any).createObjectURL = (b: Blob) => {
      blobs.push(b);
      return 'blob:s';
    };
    (URL as any).revokeObjectURL = jest.fn();
    jest.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      names.push(this.download);
    });
    fetchMock = jest.fn();
    (global as any).fetch = fetchMock;
  });

  afterEach(() => {
    jest.restoreAllMocks();
    jest.useRealTimers();
  });

  const read = (b: Blob): Promise<string> =>
    new Promise((res) => {
      const fr = new FileReader();
      fr.onload = () => res(String(fr.result));
      fr.readAsText(b);
    });

  it('slugifySkillName: кириллица отбрасывается, пустой результат -> skill-<base36>', () => {
    jest.useFakeTimers();
    jest.setSystemTime(new Date(1700000000000));
    expect(slugifySkillName('  My  Skill!! v1.2_x ')).toBe('my-skill-v1.2_x');
    expect(slugifySkillName('Навык')).toBe(`skill-${(1700000000000).toString(36)}`);
    expect(slugifySkillName('')).toBe(`skill-${(1700000000000).toString(36)}`);
    expect(slugifySkillName('a'.repeat(200))).toHaveLength(80);
  });

  it('formatSkillsApiDetail: строка, массив ошибок, объект с message, запасной текст', () => {
    expect(formatSkillsApiDetail('  oops ')).toBe('  oops ');
    expect(
      formatSkillsApiDetail([{ msg: 'bad', loc: ['body', 'name'] }, 'plain', { msg: 'x' }, 5, null]),
    ).toBe('body.name: bad; plain; x');
    expect(formatSkillsApiDetail([5, null], 'fb')).toBe('fb');
    expect(formatSkillsApiDetail({ message: 'm' })).toBe('m');
    expect(formatSkillsApiDetail({ message: '' }, 'fb')).toBe('fb');
    expect(formatSkillsApiDetail(undefined)).toBe('Ошибка');
    expect(formatSkillsApiDetail('   ', 'пусто')).toBe('пусто');
  });

  it('formToPortableSkill: подстановки, обрезка заголовка и разбор CSV', () => {
    const p = formToPortableSkill(form({ display_title: 'x'.repeat(200) }));
    expect(p.name).toBe('Мой навык');
    expect(p.slug).toMatch(/^skill-/); // кириллица -> запасной slug
    expect(p.display_title).toHaveLength(128);
    expect(p.description).toBe('описание');
    expect(p.allowed_tools).toEqual(['a', 'b', 'c', 'd']);
    expect(p.category).toBe('cat');
    expect(p.meta).toEqual({ tags: ['t1', 't2'] });
    const bare = formToPortableSkill(form({ name: ' ', slug: 'sl', description: '', category: '', is_active: false }));
    expect(bare.name).toBe('sl');
    expect(bare.slug).toBe('sl');
    expect(bare.description).toBeNull();
    expect(bare.category).toBeNull();
    expect(bare.is_active).toBe(false);
    expect(formToPortableSkill(form({ name: '', slug: '' })).name).toBe('skill');
  });

  it('portableSkillToFormFields: значения по умолчанию и склейка списков', () => {
    const f = portableSkillToFormFields({ slug: '', name: '', content: '' } as PortableSkill);
    expect(f.name).toBe('Imported skill');
    expect(f.slug).toMatch(/^imported-skill$/);
    expect(f.allowed_tools).toBe('');
    expect(f.tags).toBe('');
    expect(f.is_active).toBe(true);
    const g = portableSkillToFormFields({
      slug: 's',
      name: 'N',
      content: 'c',
      allowed_tools: ['x', 'y'],
      meta: { tags: ['a', 'b'] },
      category: 'k',
      is_public: true,
      is_active: false,
    });
    expect(g.allowed_tools).toBe('x, y');
    expect(g.tags).toBe('a, b');
    expect(g.is_public).toBe(true);
    expect(g.is_active).toBe(false);
    expect(g.category).toBe('k');
  });

  it('serializeSkillToMd: кавычит значения с двоеточием и пропускает пустые поля', () => {
    const md = serializeSkillToMd({
      slug: 's',
      name: 'a: b',
      display_title: ' lead',
      description: '',
      content: 'тело',
      category: 'c',
      allowed_tools: ['t1', 't2'],
      meta: { tags: ['x'] },
    });
    expect(md).toContain('name: "a: b"');
    expect(md).toContain('display_title: " lead"');
    expect(md).not.toContain('description:');
    expect(md).toContain('allowed_tools: t1, t2');
    expect(md).toContain('tags: x');
    expect(md.endsWith('---\n\nтело')).toBe(true);
  });

  it('parseSkillFrontmatter: без frontmatter тело возвращается целиком, с ним разбираются флаги', () => {
    expect(parseSkillFrontmatter('просто текст')).toEqual({ body: 'просто текст' });
    const r = parseSkillFrontmatter(
      '---\r\nname: "N"\r\nslug: s\r\nis_active: no\r\nis_public: yes\r\nuser_invocable: off\r\ndisable_model_invocation: 1\r\nalways_apply: maybe\r\nallowed_tools: a; b\r\ntags: x,y\r\nbad line\r\n---\r\nBODY',
    );
    expect(r.name).toBe('N');
    expect(r.is_active).toBe(false);
    expect(r.is_public).toBe(true);
    expect(r.user_invocable).toBe(false);
    expect(r.disable_model_invocation).toBe(true);
    expect(r.always_apply).toBe(false); // непонятное значение -> по умолчанию
    expect(r.allowed_tools).toEqual(['a', 'b']);
    expect(r.tags).toEqual(['x', 'y']);
    expect(r.body).toBe('BODY');
    const empty = parseSkillFrontmatter('---\nname: x\n---\n');
    expect(empty.body).toBe('');
    expect(empty.is_active).toBeUndefined();
  });

  it('exportSkill: валидирует содержимое и название', () => {
    expect(() => exportSkill({ slug: 's', name: 'n', content: '  ' }, 'json')).toThrow('Нет содержимого');
    expect(() => exportSkill({ slug: 's', name: ' ', content: 'c' }, 'json')).toThrow('Укажите название');
  });

  it('exportSkill: JSON из PortableSkill', async () => {
    exportSkill({ slug: 'my-skill', name: 'N', content: 'c' }, 'json');
    expect(names).toEqual(['my-skill.json']);
    expect(blobs[0].type).toBe('application/json');
    expect(JSON.parse(await read(blobs[0])).slug).toBe('my-skill');
    expect(URL.revokeObjectURL).toHaveBeenCalled();
  });

  it('exportSkill: Markdown из формы (allowed_tools строкой) и slug по имени', async () => {
    exportSkill(form({ name: 'Latin Name', slug: '' }), 'md');
    expect(names).toEqual(['latin-name.md']);
    expect(blobs[0].type).toBe('text/markdown;charset=utf-8');
    const md = await read(blobs[0]);
    expect(md).toContain('allowed_tools: a, b, c, d');
  });

  it('exportSkillsJson: нормализует ответ, пропускает пустые, отправляет токен', async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      json: async () => [
        { name: 'A', content: 'x', allowed_tools: 'p,q', meta: { tags: [1, 2] }, display_title: 'D', description: 'd', category: 'c' },
        { name: 'empty', content: '   ' },
        { slug: 'b', content: 'y', allowed_tools: ['z'], meta: 'bad', is_public: true },
      ],
    });
    await exportSkillsJson('tok');
    expect(fetchMock).toHaveBeenCalledWith('http://api.test/skills/export', {
      headers: { 'Content-Type': 'application/json', Authorization: 'Bearer tok' },
    });
    expect(names[0]).toMatch(/^skills-export-\d+\.json$/);
    const list = JSON.parse(await read(blobs[0]));
    expect(list).toHaveLength(2);
    expect(list[0]).toMatchObject({ name: 'A', allowed_tools: ['p', 'q'], meta: { tags: ['1', '2'] }, display_title: 'D' });
    expect(list[1]).toMatchObject({ slug: 'b', name: 'b', allowed_tools: ['z'], meta: { tags: [] }, is_public: true });
  });

  it('exportSkillsJson: одиночный объект оборачивается в список, без токена нет Authorization', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ name: 'S', content: 'c' }) });
    await exportSkillsJson(null);
    expect(fetchMock.mock.calls[0][1].headers).toEqual({ 'Content-Type': 'application/json' });
    expect(JSON.parse(await read(blobs[0]))).toHaveLength(1);
  });

  it('exportSkillsJson: ошибка HTTP -> Export failed', async () => {
    fetchMock.mockResolvedValue({ ok: false });
    await expect(exportSkillsJson('t')).rejects.toThrow('Export failed');
  });

  it('importSkillFile(.json): создаёт каждый валидный skill и сообщает об изменении', async () => {
    const events: Event[] = [];
    window.addEventListener(SKILLS_CHANGED_EVENT, (e) => events.push(e));
    fetchMock.mockResolvedValueOnce({ ok: true }).mockResolvedValueOnce({ ok: false });
    const res = await importSkillFile(
      fileOf(
        'skills.JSON',
        JSON.stringify([{ name: 'A', content: 'x' }, null, 'str', { name: 'пусто', content: '' }, { name: 'B', content: 'y' }]),
      ),
      'tok',
    );
    expect(res).toEqual({ kind: 'json', imported: 1 });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('http://api.test/skills/create');
    expect(init.method).toBe('POST');
    expect(init.headers.Authorization).toBe('Bearer tok');
    const body = JSON.parse(init.body);
    expect(body).toMatchObject({ name: 'A', content: 'x', display_title: 'A', is_active: true, allowed_tools: [], meta: { tags: [] } });
    expect(events).toHaveLength(1);
  });

  it('importSkillFile(.json): ничего не импортировано -> события нет', async () => {
    const events: Event[] = [];
    const h = (e: Event) => events.push(e);
    window.addEventListener(SKILLS_CHANGED_EVENT, h);
    const res = await importSkillFile(fileOf('x.json', JSON.stringify({ name: 'n', content: '' })), undefined);
    window.removeEventListener(SKILLS_CHANGED_EVENT, h);
    expect(res).toEqual({ kind: 'json', imported: 0 });
    expect(events).toHaveLength(0);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('importSkillFile(.md): возвращает поля формы из frontmatter', async () => {
    const res = await importSkillFile(
      fileOf('skill.md', '---\nname: Hello\nslug: hi\ntags: a,b\nallowed_tools: x\n---\nBody'),
      null,
    );
    expect(res).toMatchObject({
      kind: 'md',
      name: 'Hello',
      slug: 'hi',
      content: 'Body',
      tags: 'a, b',
      allowed_tools: 'x',
      is_active: true,
    });
  });

  it('importSkillFile(.txt) без frontmatter: имя берётся из имени файла', async () => {
    const res: any = await importSkillFile(fileOf('My Notes.txt', 'просто текст'), null);
    expect(res.kind).toBe('md');
    expect(res.name).toBe('My Notes');
    expect(res.slug).toBe('my-notes.txt');
    expect(res.content).toBe('просто текст');
  });
});
