/**
 * @jest-environment jsdom
 */
import {
  KEEP_CHAT_SETTINGS_KEY,
  isKeepChatSettingsEnabled,
  prepareNewChatSettings,
  setKeepChatSettingsEnabled,
} from '@/utils/newChatSettings';

jest.mock('@/utils/clearActiveAgent', () => ({
  clearActiveAgent: jest.fn(),
}));
jest.mock('@/utils/knowledgeRagStorage', () => ({
  setKnowledgeRagEnabled: jest.fn(),
}));
jest.mock('@/utils/skillSelectionStorage', () => ({
  copyActiveSkills: jest.fn(),
}));
jest.mock('@/mcp/selectionStorage', () => ({
  copyMcpToolIds: jest.fn(),
}));
jest.mock('@/utils/artifactsSelectionStorage', () => ({
  hasChatArtifactsOverride: jest.fn(() => true),
  getChatArtifactsToolsState: jest.fn(() => ({
    shadcn_enabled: true,
    user_prompt_mode: false,
  })),
  setChatArtifactsToolMode: jest.fn(),
  disableChatArtifactsToolsForChat: jest.fn(),
}));
jest.mock('@/coding/selectionStorage', () => ({
  isCodingModeEnabled: jest.fn(() => true),
  isCodingPlanModeEnabled: jest.fn(() => false),
  setCodingModeEnabled: jest.fn(),
  setCodingPlanModeEnabled: jest.fn(),
}));
jest.mock('@/imageGeneration/selectionStorage', () => ({
  isImageGenerationModeEnabled: jest.fn(() => true),
  isVideoGenerationModeEnabled: jest.fn(() => false),
  setImageGenerationModeEnabled: jest.fn(),
  setVideoGenerationModeEnabled: jest.fn(),
  disableAllGenerationModes: jest.fn(),
}));

import { clearActiveAgent } from '@/utils/clearActiveAgent';
import { setKnowledgeRagEnabled } from '@/utils/knowledgeRagStorage';
import { copyActiveSkills } from '@/utils/skillSelectionStorage';
import { copyMcpToolIds } from '@/mcp/selectionStorage';
import {
  hasChatArtifactsOverride,
  getChatArtifactsToolsState,
  setChatArtifactsToolMode,
} from '@/utils/artifactsSelectionStorage';
import {
  isCodingModeEnabled,
  isCodingPlanModeEnabled,
  setCodingModeEnabled,
  setCodingPlanModeEnabled,
} from '@/coding/selectionStorage';
import {
  disableAllGenerationModes,
  isImageGenerationModeEnabled,
  isVideoGenerationModeEnabled,
  setImageGenerationModeEnabled,
  setVideoGenerationModeEnabled,
} from '@/imageGeneration/selectionStorage';

describe('newChatSettings (local)', () => {
  beforeEach(() => {
    localStorage.clear();
    jest.clearAllMocks();
    (hasChatArtifactsOverride as jest.Mock).mockReturnValue(true);
    (getChatArtifactsToolsState as jest.Mock).mockReturnValue({
      shadcn_enabled: true,
      user_prompt_mode: false,
    });
    (isCodingModeEnabled as jest.Mock).mockReturnValue(true);
    (isCodingPlanModeEnabled as jest.Mock).mockReturnValue(false);
    (isImageGenerationModeEnabled as jest.Mock).mockReturnValue(true);
    (isVideoGenerationModeEnabled as jest.Mock).mockReturnValue(false);
  });

  it('stores keep flag in localStorage', () => {
    expect(isKeepChatSettingsEnabled()).toBe(false);
    setKeepChatSettingsEnabled(true);
    expect(localStorage.getItem(KEEP_CHAT_SETTINGS_KEY)).toBe('true');
    expect(isKeepChatSettingsEnabled()).toBe(true);
    setKeepChatSettingsEnabled(false);
    expect(isKeepChatSettingsEnabled()).toBe(false);
  });

  it('when disabled: clears agent, knowledge RAG and local modes', () => {
    prepareNewChatSettings('old', 'new');
    expect(clearActiveAgent).toHaveBeenCalled();
    expect(setKnowledgeRagEnabled).toHaveBeenCalledWith(false);
    expect(disableAllGenerationModes).toHaveBeenCalledWith('new');
    expect(setCodingModeEnabled).toHaveBeenCalledWith('new', false);
    expect(setCodingPlanModeEnabled).toHaveBeenCalledWith('new', false);
    expect(copyActiveSkills).not.toHaveBeenCalled();
  });

  it('when enabled: copies per-chat settings and keeps global agent/RAG', () => {
    setKeepChatSettingsEnabled(true);
    prepareNewChatSettings('old', 'new');
    expect(copyActiveSkills).toHaveBeenCalledWith('old', 'new');
    expect(copyMcpToolIds).toHaveBeenCalledWith('old', 'new');
    expect(setChatArtifactsToolMode).toHaveBeenCalledWith('new', 'shadcn_enabled', true);
    expect(setCodingModeEnabled).toHaveBeenCalledWith('new', true);
    expect(setCodingPlanModeEnabled).toHaveBeenCalledWith('new', false);
    expect(setImageGenerationModeEnabled).toHaveBeenCalledWith('new', true);
    expect(setVideoGenerationModeEnabled).toHaveBeenCalledWith('new', false);
    expect(clearActiveAgent).not.toHaveBeenCalled();
    expect(setKnowledgeRagEnabled).not.toHaveBeenCalled();
  });

  it('when enabled without prevChatId: does not copy', () => {
    setKeepChatSettingsEnabled(true);
    prepareNewChatSettings(null, 'new');
    expect(copyActiveSkills).not.toHaveBeenCalled();
    expect(clearActiveAgent).not.toHaveBeenCalled();
  });
});
