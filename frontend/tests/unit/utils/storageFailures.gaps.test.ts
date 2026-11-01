import {
  readAgentArtifactsDefaults,
  readAgentArtifactsEnabled,
  readAgentPresentationSkills,
} from '@/utils/agentArtifactsEnabled';
import { hasChatArtifactsOverride } from '@/utils/artifactsSelectionStorage';
import { isKeepChatSettingsEnabled, setKeepChatSettingsEnabled } from '@/utils/newChatSettings';
import { clearActiveSkills, getActiveSkillRefs, renameSkillSlugInAllChats, setActiveSkillIds } from '@/utils/skillSelectionStorage';
import { rewriteHtmlArtifactScriptsForOffline } from '@/utils/htmlArtifactScripts';

describe('утилиты localStorage: недоступное хранилище и крайние случаи', () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => jest.restoreAllMocks());

  const breakStorage = () => {
    jest.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('denied');
    });
    jest.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('denied');
    });
  };

  it('agentArtifactsEnabled: при ошибке чтения везде false', () => {
    breakStorage();
    expect(readAgentArtifactsEnabled()).toBe(false);
    expect(readAgentArtifactsDefaults()).toEqual({
      artifacts_enabled: false,
      shadcn_enabled: false,
      user_prompt_mode: false,
    });
    expect(readAgentPresentationSkills()).toBe(false);
  });

  it('hasChatArtifactsOverride: false без chatId и при ошибке хранилища', () => {
    expect(hasChatArtifactsOverride(null)).toBe(false);
    expect(hasChatArtifactsOverride('')).toBe(false);
    breakStorage();
    expect(hasChatArtifactsOverride('c1')).toBe(false);
  });

  it('newChatSettings: флаг переноса настроек читается/пишется и переживает ошибки', () => {
    expect(isKeepChatSettingsEnabled()).toBe(false);
    setKeepChatSettingsEnabled(true);
    expect(isKeepChatSettingsEnabled()).toBe(true);
    breakStorage();
    expect(isKeepChatSettingsEnabled()).toBe(false);
    expect(() => setKeepChatSettingsEnabled(false)).not.toThrow();
  });

  it('skillSelectionStorage: clearActiveSkills без chatId ничего не делает, с chatId очищает выбор', () => {
    setActiveSkillIds('chat-1', ['a', 'b']);
    clearActiveSkills(null);
    clearActiveSkills(undefined);
    expect(getActiveSkillRefs('chat-1').map((r) => r.slug)).toEqual(['a', 'b']);
    clearActiveSkills('chat-1');
    expect(getActiveSkillRefs('chat-1')).toEqual([]);
  });

  it('skillSelectionStorage: rename пропускает ключи с пустым chatId и чужие ключи', () => {
    localStorage.setItem('chat::active_skill_ids', JSON.stringify(['old']));
    localStorage.setItem('other:key', 'x');
    setActiveSkillIds('c1', ['old', 'keep']);
    renameSkillSlugInAllChats('old', 'new', 'Новый');
    expect(getActiveSkillRefs('c1').map((r) => r.slug)).toEqual(['new', 'keep']);
    expect(localStorage.getItem('chat::active_skill_ids')).toBe(JSON.stringify(['old']));
  });

  it('htmlArtifactScripts: если путь локального chart уже упомянут в тексте, скрипт повторно не вставляется', () => {
    // Упоминание вне <script src>, поэтому регулярка удаления его не убирает.
    const html = '<html><head></head><body><!-- /vendor/chart.umd.min.js --><script>new Chart(c, {})</script></body></html>';
    expect(rewriteHtmlArtifactScriptsForOffline(html)).toBe(html);
  });
});
