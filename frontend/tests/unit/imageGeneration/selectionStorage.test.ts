/**
 * @jest-environment jsdom
 */
import {
  disableAllGenerationModes,
  isAnyGenerationModeEnabled,
  isImageGenerationModeEnabled,
  isVideoGenerationModeEnabled,
  setImageGenerationModeEnabled,
  setVideoGenerationModeEnabled,
} from '@/imageGeneration/selectionStorage';

describe('imageGeneration selectionStorage (local)', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('stores image and video modes per chat', () => {
    setImageGenerationModeEnabled('c1', true);
    expect(isImageGenerationModeEnabled('c1')).toBe(true);
    expect(isVideoGenerationModeEnabled('c1')).toBe(false);
    expect(isAnyGenerationModeEnabled('c1')).toBe(true);

    setVideoGenerationModeEnabled('c1', true);
    expect(isVideoGenerationModeEnabled('c1')).toBe(true);

    disableAllGenerationModes('c1');
    expect(isAnyGenerationModeEnabled('c1')).toBe(false);
  });
});
