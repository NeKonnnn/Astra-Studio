/** Import / export skills — общий код для SkillsPage и SkillsSidebarPanel. */

import { getApiUrl, API_ENDPOINTS } from '../config/api';

export const SKILLS_CHANGED_EVENT = 'astrachatSkillsChanged';

export function notifySkillsChanged(): void {
  window.dispatchEvent(new CustomEvent(SKILLS_CHANGED_EVENT));
}

/** Как backend slugify_skill_id: только a-z0-9._- (кириллица отбрасывается). */
export function slugifySkillName(name: string): string {
  const s = (name || '')
    .trim()
    .toLowerCase()
    .replace(/\s+/g, '-')
    .replace(/[^a-z0-9._-]+/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 80);
  return s || `skill-${Date.now().toString(36)}`;
}

export function formatSkillsApiDetail(detail: unknown, fallback = 'Ошибка'): string {
  if (typeof detail === 'string' && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    const parts = detail
      .map((item) => {
        if (typeof item === 'string') return item;
        if (item && typeof item === 'object') {
          const o = item as { msg?: string; loc?: unknown[] };
          const loc = Array.isArray(o.loc) ? o.loc.join('.') : '';
          return [loc, o.msg].filter(Boolean).join(': ');
        }
        return '';
      })
      .filter(Boolean);
    if (parts.length) return parts.join('; ');
  }
  if (detail && typeof detail === 'object' && 'message' in detail) {
    return String((detail as { message: unknown }).message || fallback);
  }
  return fallback;
}

/** Портативный payload skill для JSON/MD round-trip (без id/author/ratings). */
export type PortableSkill = {
  slug: string;
  name: string;
  display_title?: string | null;
  description?: string | null;
  content: string;
  is_active?: boolean;
  is_public?: boolean;
  user_invocable?: boolean;
  disable_model_invocation?: boolean;
  always_apply?: boolean;
  allowed_tools?: string[];
  category?: string | null;
  meta?: { tags?: string[] };
};

/** Поля формы сайдбара → портативный skill. */
export type SkillFormLike = {
  slug: string;
  name: string;
  display_title: string;
  description: string;
  content: string;
  is_active: boolean;
  is_public: boolean;
  user_invocable: boolean;
  disable_model_invocation: boolean;
  always_apply: boolean;
  /** Через запятую, как в UI. */
  allowed_tools: string;
  category: string;
  tags: string;
};

function splitCsv(raw: string): string[] {
  return raw
    .split(/[,;\n]/)
    .map((s) => s.trim())
    .filter(Boolean);
}

function parseBool(raw: string | undefined, fallback: boolean): boolean {
  if (raw == null || raw === '') return fallback;
  const v = raw.trim().toLowerCase();
  if (['true', 'yes', '1', 'on'].includes(v)) return true;
  if (['false', 'no', '0', 'off'].includes(v)) return false;
  return fallback;
}

export function formToPortableSkill(form: SkillFormLike): PortableSkill {
  const name = form.name.trim() || form.slug.trim() || 'skill';
  const slug = form.slug.trim() || slugifySkillName(name);
  const tags = splitCsv(form.tags);
  return {
    slug,
    name,
    display_title: (form.display_title.trim() || name).slice(0, 128),
    description: form.description.trim() || null,
    content: form.content,
    is_active: form.is_active !== false,
    is_public: Boolean(form.is_public),
    user_invocable: form.user_invocable !== false,
    disable_model_invocation: Boolean(form.disable_model_invocation),
    always_apply: Boolean(form.always_apply),
    allowed_tools: splitCsv(form.allowed_tools),
    category: form.category.trim() || null,
    meta: { tags },
  };
}

export function portableSkillToFormFields(skill: PortableSkill): SkillFormLike {
  const name = skill.name || skill.slug || 'Imported skill';
  return {
    slug: skill.slug || slugifySkillName(name),
    name,
    display_title: (skill.display_title || name).slice(0, 128),
    description: skill.description || '',
    content: skill.content || '',
    is_active: skill.is_active !== false,
    is_public: Boolean(skill.is_public),
    user_invocable: skill.user_invocable !== false,
    disable_model_invocation: Boolean(skill.disable_model_invocation),
    always_apply: Boolean(skill.always_apply),
    allowed_tools: (skill.allowed_tools || []).join(', '),
    category: skill.category || '',
    tags: (skill.meta?.tags || []).join(', '),
  };
}

function downloadBlob(blob: Blob, filename: string): void {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}

function yamlQuote(value: string): string {
  if (
    /[:#\[\]{}|>*&!%@`\r\n]/.test(value) ||
    /^\s|\s$/.test(value) ||
    /^["']/.test(value) ||
    value === ''
  ) {
    return JSON.stringify(value);
  }
  return value;
}

/** Обратная операция к yamlQuote: JSON-строка → значение, иначе — как есть. */
function yamlUnquote(raw: string): string {
  const v = raw.trim();
  if (v.length >= 2 && v.startsWith('"') && v.endsWith('"')) {
    try {
      const parsed = JSON.parse(v);
      if (typeof parsed === 'string') return parsed;
    } catch {
      /* не валидный JSON — ниже снимаем обрамляющие кавычки */
    }
    return v.slice(1, -1);
  }
  if (v.length >= 2 && v.startsWith("'") && v.endsWith("'")) {
    return v.slice(1, -1);
  }
  return v;
}

/** SKILL.md: YAML frontmatter + body. */
export function serializeSkillToMd(skill: PortableSkill): string {
  const lines: string[] = ['---'];
  lines.push(`name: ${yamlQuote(skill.name)}`);
  if (skill.slug) lines.push(`slug: ${yamlQuote(skill.slug)}`);
  if (skill.display_title) lines.push(`display_title: ${yamlQuote(skill.display_title)}`);
  if (skill.description) lines.push(`description: ${yamlQuote(skill.description)}`);
  if (skill.category) lines.push(`category: ${yamlQuote(skill.category)}`);
  lines.push(`is_active: ${skill.is_active !== false}`);
  lines.push(`is_public: ${Boolean(skill.is_public)}`);
  lines.push(`user_invocable: ${skill.user_invocable !== false}`);
  lines.push(`disable_model_invocation: ${Boolean(skill.disable_model_invocation)}`);
  lines.push(`always_apply: ${Boolean(skill.always_apply)}`);
  const tools = skill.allowed_tools || [];
  if (tools.length) lines.push(`allowed_tools: ${yamlQuote(tools.join(', '))}`);
  const tags = skill.meta?.tags || [];
  if (tags.length) lines.push(`tags: ${yamlQuote(tags.join(', '))}`);
  lines.push('---');
  lines.push('');
  lines.push(skill.content || '');
  return lines.join('\n');
}

export function parseSkillFrontmatter(md: string): {
  name?: string;
  slug?: string;
  display_title?: string;
  description?: string;
  category?: string;
  is_active?: boolean;
  is_public?: boolean;
  user_invocable?: boolean;
  disable_model_invocation?: boolean;
  always_apply?: boolean;
  allowed_tools?: string[];
  tags?: string[];
  body: string;
} {
  const m = md.match(/^---\r?\n([\s\S]*?)\r?\n---\r?\n?([\s\S]*)$/);
  if (!m) return { body: md };
  const meta: Record<string, string> = {};
  for (const line of m[1].split(/\r?\n/)) {
    const idx = line.indexOf(':');
    if (idx > 0) {
      meta[line.slice(0, idx).trim()] = yamlUnquote(line.slice(idx + 1));
    }
  }
  // serializeSkillToMd вставляет ровно одну пустую строку-разделитель после frontmatter.
  const body = m[2].replace(/^\r?\n/, '');
  return {
    name: meta.name,
    slug: meta.slug,
    display_title: meta.display_title,
    description: meta.description,
    category: meta.category,
    is_active: meta.is_active != null ? parseBool(meta.is_active, true) : undefined,
    is_public: meta.is_public != null ? parseBool(meta.is_public, false) : undefined,
    user_invocable: meta.user_invocable != null ? parseBool(meta.user_invocable, true) : undefined,
    disable_model_invocation:
      meta.disable_model_invocation != null
        ? parseBool(meta.disable_model_invocation, false)
        : undefined,
    always_apply: meta.always_apply != null ? parseBool(meta.always_apply, false) : undefined,
    allowed_tools: meta.allowed_tools ? splitCsv(meta.allowed_tools) : undefined,
    tags: meta.tags ? splitCsv(meta.tags) : undefined,
    body,
  };
}

function normalizeImportedItem(item: Record<string, unknown>): PortableSkill | null {
  const name = String(item.name || item.slug || 'Imported skill');
  const content = String(item.content ?? '');
  if (!content.trim()) return null;
  const meta =
    item.meta && typeof item.meta === 'object'
      ? (item.meta as { tags?: string[] })
      : { tags: [] };
  const allowed = Array.isArray(item.allowed_tools)
    ? (item.allowed_tools as unknown[]).map(String)
    : typeof item.allowed_tools === 'string'
      ? splitCsv(item.allowed_tools)
      : [];
  return {
    slug: String(item.slug || slugifySkillName(name)),
    name,
    display_title: item.display_title != null ? String(item.display_title) : name,
    description: item.description != null ? String(item.description) : null,
    content,
    is_active: item.is_active !== false,
    is_public: Boolean(item.is_public),
    user_invocable: item.user_invocable !== false,
    disable_model_invocation: Boolean(item.disable_model_invocation),
    always_apply: Boolean(item.always_apply),
    allowed_tools: allowed,
    category: item.category != null ? String(item.category) : null,
    meta: { tags: Array.isArray(meta.tags) ? meta.tags.map(String) : [] },
  };
}

function createPayloadFromPortable(skill: PortableSkill) {
  return {
    slug: skill.slug,
    name: skill.name,
    display_title: skill.display_title || skill.name,
    description: skill.description || null,
    content: skill.content,
    is_active: skill.is_active !== false,
    is_public: Boolean(skill.is_public),
    user_invocable: skill.user_invocable !== false,
    disable_model_invocation: Boolean(skill.disable_model_invocation),
    always_apply: Boolean(skill.always_apply),
    allowed_tools: skill.allowed_tools || [],
    category: skill.category || null,
    meta: skill.meta || { tags: [] },
  };
}

export type SkillExportFormat = 'json' | 'md';

/** Экспорт одного skill (текущего) в JSON или Markdown. */
export function exportSkill(
  skill: PortableSkill | SkillFormLike,
  format: SkillExportFormat,
): void {
  const portable =
    'allowed_tools' in skill && typeof skill.allowed_tools === 'string'
      ? formToPortableSkill(skill as SkillFormLike)
      : (skill as PortableSkill);
  if (!portable.content?.trim()) {
    throw new Error('Нет содержимого skill для экспорта');
  }
  if (!portable.name?.trim()) {
    throw new Error('Укажите название skill перед экспортом');
  }
  const base = portable.slug || slugifySkillName(portable.name);
  if (format === 'md') {
    const blob = new Blob([serializeSkillToMd(portable)], {
      type: 'text/markdown;charset=utf-8',
    });
    downloadBlob(blob, `${base}.md`);
    return;
  }
  const blob = new Blob([JSON.stringify(portable, null, 2)], {
    type: 'application/json',
  });
  downloadBlob(blob, `${base}.json`);
}

function authHeaders(token: string | null | undefined): HeadersInit {
  const h: HeadersInit = { 'Content-Type': 'application/json' };
  if (token) h.Authorization = `Bearer ${token}`;
  return h;
}

/** @deprecated Используйте exportSkill — bulk export всех skills. */
export async function exportSkillsJson(token: string | null | undefined): Promise<void> {
  const resp = await fetch(`${getApiUrl(API_ENDPOINTS.SKILLS)}/export`, {
    headers: authHeaders(token),
  });
  if (!resp.ok) throw new Error('Export failed');
  const data = await resp.json();
  const list = Array.isArray(data) ? data : [data];
  const portable = list
    .map((item) => normalizeImportedItem(item as Record<string, unknown>))
    .filter((s): s is PortableSkill => Boolean(s));
  const blob = new Blob([JSON.stringify(portable, null, 2)], { type: 'application/json' });
  downloadBlob(blob, `skills-export-${Date.now()}.json`);
}

export type SkillMdImportResult = {
  kind: 'md';
} & SkillFormLike;

export type SkillJsonImportResult = {
  kind: 'json';
  imported: number;
};

export type SkillImportResult = SkillMdImportResult | SkillJsonImportResult;

/** Импорт .json (создаёт skills) или .md/.txt (возвращает поля для формы). */
export async function importSkillFile(
  file: File,
  token: string | null | undefined,
): Promise<SkillImportResult> {
  const text = await file.text();
  const lower = file.name.toLowerCase();

  if (lower.endsWith('.json')) {
    const parsed = JSON.parse(text);
    const list = Array.isArray(parsed) ? parsed : [parsed];
    let imported = 0;
    for (const raw of list) {
      if (!raw || typeof raw !== 'object') continue;
      const skill = normalizeImportedItem(raw as Record<string, unknown>);
      if (!skill) continue;
      const resp = await fetch(`${getApiUrl(API_ENDPOINTS.SKILLS)}/create`, {
        method: 'POST',
        headers: authHeaders(token),
        body: JSON.stringify(createPayloadFromPortable(skill)),
      });
      if (resp.ok) imported += 1;
    }
    if (imported > 0) notifySkillsChanged();
    return { kind: 'json', imported };
  }

  const parsed = parseSkillFrontmatter(text);
  const displayName =
    parsed.name || file.name.replace(/\.(md|txt)$/i, '') || 'Imported skill';
  const portable: PortableSkill = {
    slug: parsed.slug || slugifySkillName(parsed.name || file.name),
    name: displayName,
    display_title: parsed.display_title || displayName,
    description: parsed.description || null,
    content: parsed.body,
    is_active: parsed.is_active !== false,
    is_public: Boolean(parsed.is_public),
    user_invocable: parsed.user_invocable !== false,
    disable_model_invocation: Boolean(parsed.disable_model_invocation),
    always_apply: Boolean(parsed.always_apply),
    allowed_tools: parsed.allowed_tools || [],
    category: parsed.category || null,
    meta: { tags: parsed.tags || [] },
  };
  return { kind: 'md', ...portableSkillToFormFields(portable) };
}
