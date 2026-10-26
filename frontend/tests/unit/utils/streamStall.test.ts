/**
 * @jest-environment jsdom
 */
import {
  STREAM_STALL_AFTER_CHUNK_MS,
  STREAM_STALL_BEFORE_CHUNK_MS,
  STREAM_STALL_MEDIA_MS,
  resolveStreamStallLimitMs,
} from '@/utils/streamStall';

describe('resolveStreamStallLimitMs', () => {
  it('uses long media limit for image/video generation', () => {
    expect(resolveStreamStallLimitMs({ mediaGeneration: true, receivedStreamData: true })).toBe(
      STREAM_STALL_MEDIA_MS,
    );
    expect(STREAM_STALL_MEDIA_MS).toBeGreaterThanOrEqual(600_000);
  });

  it('uses short limit after first text chunk', () => {
    expect(resolveStreamStallLimitMs({ receivedStreamData: true })).toBe(STREAM_STALL_AFTER_CHUNK_MS);
  });

  it('uses longer pre-chunk limit while waiting for first token', () => {
    expect(resolveStreamStallLimitMs({})).toBe(STREAM_STALL_BEFORE_CHUNK_MS);
  });
});
