/**
 * @jest-environment jsdom
 */
import {
  parseSkillFrontmatter,
  serializeSkillToMd,
  type PortableSkill,
} from '@/utils/skillsImportExport';

jest.mock('@/config/api', () => ({
  getApiUrl: (path: string) => path,
  API_ENDPOINTS: { SKILLS: '/api/skills' },
}));

const baseSkill = (overrides: Partial<PortableSkill> = {}): PortableSkill => ({
  slug: 'my-skill',
  name: 'My Skill',
  display_title: 'My Skill',
  description: 'Simple description',
  content: '# Title\n\nBody text',
  is_active: true,
  is_public: false,
  user_invocable: true,
  disable_model_invocation: false,
  always_apply: false,
  allowed_tools: [],
  category: null,
  meta: { tags: [] },
  ...overrides,
});

describe('skillsImportExport markdown roundtrip', () => {
  it('does not add a leading newline to the body on export → import', () => {
    const skill = baseSkill();
    const parsed = parseSkillFrontmatter(serializeSkillToMd(skill));
    expect(parsed.body).toBe(skill.content);
  });

  it('is stable across repeated roundtrips (no growing leading newlines)', () => {
    let content = '# Title\n\nBody text';
    for (let i = 0; i < 3; i += 1) {
      const md = serializeSkillToMd(baseSkill({ content }));
      content = parseSkillFrontmatter(md).body;
    }
    expect(content).toBe('# Title\n\nBody text');
  });

  it('preserves an intentionally leading newline in the content', () => {
    const skill = baseSkill({ content: '\nstarts with blank line' });
    const parsed = parseSkillFrontmatter(serializeSkillToMd(skill));
    expect(parsed.body).toBe(skill.content);
  });

  it('roundtrips empty content', () => {
    const parsed = parseSkillFrontmatter(serializeSkillToMd(baseSkill({ content: '' })));
    expect(parsed.body).toBe('');
  });

  it('parses hand-written frontmatter without blank separator line', () => {
    const parsed = parseSkillFrontmatter('---\nname: X\n---\n# Heading');
    expect(parsed.name).toBe('X');
    expect(parsed.body).toBe('# Heading');
  });

  it('returns the whole text as body when there is no frontmatter', () => {
    expect(parseSkillFrontmatter('just text').body).toBe('just text');
  });
});

describe('skillsImportExport JSON-quoted special characters', () => {
  it('JSON.parses quoted values containing colons, quotes, backslashes and unicode', () => {
    const description = 'Use: "quotes", back\\slash, #hash and Привет';
    const skill = baseSkill({ description });
    const parsed = parseSkillFrontmatter(serializeSkillToMd(skill));
    expect(parsed.description).toBe(description);
  });

  it('roundtrips multi-line values via JSON escaping', () => {
    const description = 'line one\nline two';
    const parsed = parseSkillFrontmatter(
      serializeSkillToMd(baseSkill({ description })),
    );
    expect(parsed.description).toBe(description);
  });

  it('roundtrips values with leading/trailing whitespace', () => {
    const parsed = parseSkillFrontmatter(
      serializeSkillToMd(baseSkill({ name: '  padded  ' })),
    );
    expect(parsed.name).toBe('  padded  ');
  });

  it('roundtrips values that start or end with a quote character', () => {
    const quoted = parseSkillFrontmatter(
      serializeSkillToMd(baseSkill({ description: '"quoted"' })),
    );
    expect(quoted.description).toBe('"quoted"');

    const inner = parseSkillFrontmatter(
      serializeSkillToMd(baseSkill({ description: 'say "hi"' })),
    );
    expect(inner.description).toBe('say "hi"');
  });

  it('roundtrips tags and allowed_tools containing special characters', () => {
    const parsed = parseSkillFrontmatter(
      serializeSkillToMd(
        baseSkill({ allowed_tools: ['read: file', 'write'], meta: { tags: ['a#b', 'c'] } }),
      ),
    );
    expect(parsed.allowed_tools).toEqual(['read: file', 'write']);
    expect(parsed.tags).toEqual(['a#b', 'c']);
  });

  it('keeps single-quoted hand-written values working', () => {
    expect(parseSkillFrontmatter("---\nname: 'Hello'\n---\nx").name).toBe('Hello');
  });

  it('falls back to stripping quotes for malformed JSON strings', () => {
    expect(parseSkillFrontmatter('---\nname: "bad\\q"\n---\nx').name).toBe('bad\\q');
  });
});
