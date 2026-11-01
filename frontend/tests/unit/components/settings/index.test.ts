import * as barrel from '@/components/settings/index';
import GeneralSettings from '@/components/settings/GeneralSettings';
import ProfileSettings from '@/components/settings/ProfileSettings';
import ModelsSettings from '@/components/settings/ModelsSettings';
import RAGSettings from '@/components/settings/RAGSettings';
import TranscriptionSettings from '@/components/settings/TranscriptionSettings';
import AboutSettings from '@/components/settings/AboutSettings';
import ChatSettings from '@/components/settings/ChatSettings';
import InterfaceSettings from '@/components/settings/InterfaceSettings';
import ImageGenerationSettingsSection from '@/components/settings/ImageGenerationSettingsSection';

jest.mock('@/contexts/AppContext', () => ({}));
jest.mock('@/contexts/AuthContext', () => ({}));
jest.mock('@/contexts/RagReindexStatusContext', () => ({}));
jest.mock('@/components/ManageSharesDialog', () => ({ __esModule: true, default: () => null }));
jest.mock('@/components/ModelSelector', () => ({ __esModule: true, default: () => null }));
jest.mock('@/components/ModelSettingsFields', () => ({ __esModule: true, default: () => null }));
jest.mock('@/components/RagModelSelector', () => ({ __esModule: true, default: () => null }));
jest.mock('@/components/MemoryRagLibraryModal', () => ({ __esModule: true, default: () => null }));
jest.mock('@/components/UserAvatar', () => ({ __esModule: true, default: () => null }));

describe('components/settings barrel', () => {
  it('exports every settings tab under its component name', () => {
    expect(barrel.GeneralSettings).toBe(GeneralSettings);
    expect(barrel.ProfileSettings).toBe(ProfileSettings);
    expect(barrel.ModelsSettings).toBe(ModelsSettings);
    expect(barrel.RAGSettings).toBe(RAGSettings);
    expect(barrel.TranscriptionSettings).toBe(TranscriptionSettings);
    expect(barrel.AboutSettings).toBe(AboutSettings);
    expect(barrel.ChatSettings).toBe(ChatSettings);
    expect(barrel.InterfaceSettings).toBe(InterfaceSettings);
    expect(barrel.ImageGenerationSettingsSection).toBe(ImageGenerationSettingsSection);
    expect(Object.keys(barrel).sort()).toEqual([
      'AboutSettings',
      'ChatSettings',
      'GeneralSettings',
      'ImageGenerationSettingsSection',
      'InterfaceSettings',
      'ModelsSettings',
      'ProfileSettings',
      'RAGSettings',
      'TranscriptionSettings',
    ]);
  });
});
