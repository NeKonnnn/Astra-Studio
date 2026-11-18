import type { VoiceGearPanel, VoiceIntentId, VoiceSettingsSection } from './types';

export type IntentPattern = {
  id: VoiceIntentId;
  /** Regex по нормализованному тексту (без пунктуации). */
  patterns: RegExp[];
  confirmPhrase: string;
  /** Извлечь слоты из matched текста. */
  extract?: (text: string, match: RegExpExecArray) => Record<string, unknown>;
};

function mapSettingsSection(text: string): VoiceSettingsSection | undefined {
  if (/голос/.test(text)) return 'voiceAssistant';
  if (/общ/.test(text)) return 'general';
  if (/профил/.test(text)) return 'profile';
  if (/интерф/.test(text)) return 'interface';
  if (/чат/.test(text)) return 'chats';
  if (/модел/.test(text)) return 'models';
  if (/\brag\b/.test(text)) return 'rag';
  if (/транскриб/.test(text)) return 'transcription';
  if (/о\s+приложен/.test(text) || /^about$/.test(text)) return 'about';
  return undefined;
}

function mapToolsPanel(text: string): VoiceGearPanel {
  if (/артефакт/.test(text)) return 'artifacts';
  if (/skill|навык/.test(text)) return 'skills';
  if (/баз|knowledge/.test(text)) return 'knowledge-bases';
  if (/\bmcp\b/.test(text)) return 'mcp';
  if (/coding|кодинг|код/.test(text)) return 'coding';
  if (/генерац/.test(text)) return 'generation';
  if (/режим\s+модели|model\s*mode|мышлен/.test(text)) return 'model-mode';
  if (/агент/.test(text)) return 'agents';
  return 'main';
}

/** Каталог быстрых локальных интентов (без LLM). Порядок важен — первое совпадение. */
export const INTENT_PATTERNS: IntentPattern[] = [
  {
    id: 'disableAssistant',
    patterns: [
      /^(выключи|отключи|останови)\s+(голосового\s+)?(помощника|ассистента)$/,
      /^хватит\s+слушать$/,
      /^disable\s+assistant$/,
    ],
    confirmPhrase: 'Голосовой помощник выключен',
  },
  {
    id: 'stopGeneration',
    patterns: [
      /^(останови|стоп|прекрати)(\s+(генерацию|ответ|отвечать))?$/,
      /^stop(\s+generation)?$/,
    ],
    confirmPhrase: 'Останавливаю',
  },
  {
    id: 'createProject',
    patterns: [
      /^(создай|создать|новый)\s+проект$/,
      /^(открой|открыть)\s+(меню\s+)?(создания\s+)?проекта$/,
      /^new\s+project$/,
    ],
    confirmPhrase: 'Открываю создание проекта',
  },
  {
    id: 'openProject',
    patterns: [
      /^(открой|открыть|перейди\s+в|покажи)\s+проект\s+(.+)$/,
      /^проект\s+(.+)$/,
    ],
    confirmPhrase: 'Открываю проект',
    extract: (_text, match) => ({
      projectName: String(match[2] || match[1] || '').trim(),
    }),
  },
  {
    id: 'openSettings',
    patterns: [
      /^(открой|открыть)\s+настройки\s+(общее|общие|профиль|интерфейс|чаты|модели|rag|транскрибаци\w*|голосовой\s+помощник|о\s+приложении)$/,
      /^(открой|открыть)\s+(общее|профиль|интерфейс|чаты|модели|rag|транскрибаци\w*|голосовой\s+помощник)\s+в\s+настройках$/,
      /^(открой|открыть)\s+настройки$/,
      /^настройки$/,
    ],
    confirmPhrase: 'Открываю настройки',
    extract: (text) => {
      const section = mapSettingsSection(text);
      return section ? { section, label: section } : {};
    },
  },
  {
    id: 'openToolsPanel',
    patterns: [
      /^(открой|открыть|покажи)\s+(в\s+инструментах\s+)?(агентов|агенты|артефакты|skills?|навыки|базы\s+знаний|базу\s+знаний|mcp|coding|кодинг|режим\s+генерации|генерацию|режим\s+модели)$/,
      /^(открой|открыть)\s+инструменты\s+(агентов|агенты|артефакты|skills?|навыки|базы\s+знаний|mcp|coding|кодинг|генераци\w*|режим\s+модели)$/,
    ],
    confirmPhrase: 'Открываю раздел',
    extract: (text) => {
      const panel = mapToolsPanel(text);
      return { panel, label: panel };
    },
  },
  {
    id: 'openTools',
    patterns: [
      /^(открой|открыть|покажи)\s+инструменты$/,
      /^инструменты$/,
    ],
    confirmPhrase: 'Открываю инструменты',
  },
  {
    id: 'newChat',
    patterns: [
      /^(создай|открой|новый)\s+(чат|диалог)$/,
      /^новый\s+чат$/,
      /^new\s+chat$/,
    ],
    confirmPhrase: 'Создаю чат',
  },
  {
    id: 'searchChats',
    patterns: [/^(найди|поиск|ищи)\s+(в\s+)?чат(ах|ы)?$/, /^поиск\s+чатов$/],
    confirmPhrase: 'Открываю поиск',
  },
  {
    id: 'deleteChat',
    patterns: [/^(удали|удалить)\s+(текущий\s+)?чат$/, /^delete\s+chat$/],
    confirmPhrase: 'Удаляю чат',
  },
  {
    id: 'openAgentConstructor',
    patterns: [
      /^(открой|открыть)\s+конструктор\s+агента$/,
      /^конструктор\s+агента$/,
    ],
    confirmPhrase: 'Открываю конструктор агента',
  },
  {
    id: 'openTranscription',
    patterns: [
      /^(открой|открыть)\s+транскриб(атор|ацию)$/,
      /^транскрибатор$/,
    ],
    confirmPhrase: 'Открываю транскрибатор',
  },
  {
    id: 'openSkills',
    patterns: [
      /^(открой|открыть)\s+(редактор|сайдбар|панель\s+редактора)\s+(skills?|навыков|навыки)$/,
    ],
    confirmPhrase: 'Открываю редактор навыков',
  },
  {
    id: 'openKnowledgeBases',
    patterns: [
      /^(открой|открыть)\s+(редактор|сайдбар|панель\s+редактора)\s+(баз[ыу]\s+знаний|knowledge\s*bases?)$/,
    ],
    confirmPhrase: 'Открываю редактор баз знаний',
  },
  {
    id: 'clearAgent',
    patterns: [
      /^(сбрось|убери|отключи|очисти)\s+агента$/,
      /^без\s+агента$/,
    ],
    confirmPhrase: 'Агент сброшен',
  },
  {
    id: 'readLastReply',
    patterns: [
      /^(прочитай|озвучь|прочитай\s+вслух)\s+(последн(ий|ий)\s+)?(ответ|сообщение)$/,
      /^прочитай\s+ответ$/,
    ],
    confirmPhrase: 'Читаю ответ',
  },
  {
    id: 'navigate',
    patterns: [
      /^(открой|перейди\s+(в|на)|покажи)\s+(галерею\s+агентов|страницу\s+агентов)$/,
      /^(открой|перейди\s+(в|на)|покажи)\s+(страницу\s+)?(skills|навыков)$/,
      /^(открой|перейди\s+(в|на)|покажи)\s+(страницу\s+)?(баз\s+знаний|knowledge)$/,
      /^(открой|перейди\s+(в|на)|покажи)\s+(плагины|plugins)$/,
      /^(открой|перейди\s+(в|на)|покажи)\s+(админк[уа]|admin)$/,
      /^(открой|перейди\s+(в|на)|покажи)\s+(главн(ую|ая)|чат)$/,
    ],
    confirmPhrase: 'Перехожу',
    extract: (text) => {
      if (/агент/.test(text)) return { path: '/agents', label: 'агенты' };
      if (/skill|навык/.test(text)) return { path: '/skills', label: 'навыки' };
      if (/баз|knowledge/.test(text)) return { path: '/knowledge-bases', label: 'базы знаний' };
      if (/плагин|plugin/.test(text)) return { path: '/plugins', label: 'плагины' };
      if (/админ|admin/.test(text)) return { path: '/admin', label: 'админка' };
      return { path: '/', label: 'чат' };
    },
  },
  {
    id: 'selectAgent',
    patterns: [
      /^(выбери|включи|поставь|используй|открой|открыть)\s+агента\s+(.+)$/,
      /^агент\s+(.+)$/,
    ],
    confirmPhrase: 'Выбираю агента',
    extract: (_text, match) => ({ agentName: String(match[2] || match[1] || '').trim() }),
  },
  {
    id: 'selectSkill',
    patterns: [
      /^(выбери|включи|поставь|используй|открой|открыть)\s+(skill|скилл|навык)\s+(.+)$/,
      /^(выбери|включи|поставь|используй)\s+навык\s+(.+)$/,
    ],
    confirmPhrase: 'Выбираю навык',
    extract: (_text, match) => ({
      skillName: String(match[3] || match[2] || '').trim(),
      enabled: true,
    }),
  },
  {
    id: 'selectKnowledgeBase',
    patterns: [
      /^(выбери|включи|поставь|используй|открой|открыть)\s+(базу\s+знаний|базу|knowledge\s*base)\s+(.+)$/,
    ],
    confirmPhrase: 'Выбираю базу знаний',
    extract: (_text, match) => ({
      knowledgeBaseName: String(match[3] || '').trim(),
      enabled: true,
    }),
  },
  {
    id: 'toggleMcp',
    patterns: [
      /^(открой|открыть|используй|выбери)\s+mcp(\s+сервер(а)?)?\s+(.+)$/,
      /^(включи|подключи|выключи|отключи)\s+mcp(\s+сервер(а)?)?\s+(.+)$/,
      /^(включи|подключи|выключи|отключи)\s+(сервер(а)?\s+)?(.+)$/,
      /^(enable|disable)\s+mcp\s+(.+)$/,
    ],
    confirmPhrase: 'Обновляю MCP',
    extract: (_text, match) => {
      const action = match[1] || '';
      const enabled = /включи|подключи|открой|открыть|используй|выбери|enable/i.test(action);
      let server = String(match[match.length - 1] || '')
        .replace(/^mcp\s+/i, '')
        .replace(/^(сервер(а)?|server)\s+/i, '')
        .trim();
      return { enabled, server };
    },
  },
  {
    id: 'sendMessage',
    patterns: [
      /^(напиши|отправь|скажи)\s+(.+)$/,
      /^(send|write)\s+(.+)$/,
    ],
    confirmPhrase: 'Отправляю',
    extract: (_text, match) => ({ text: String(match[2] || '').trim() }),
  },
];

export const INTENT_CONFIRM: Partial<Record<VoiceIntentId, string>> = Object.fromEntries(
  INTENT_PATTERNS.map((p) => [p.id, p.confirmPhrase]),
);
