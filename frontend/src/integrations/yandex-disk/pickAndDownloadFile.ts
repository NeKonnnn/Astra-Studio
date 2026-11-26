import { getYandexDiskAuthToken } from './oauth';
import type { YandexDiskPickedFile } from './types';

/**
 * Оркестрация как у OneDrive `pickAndDownloadFile`:
 * OAuth → UI picker (управляется снаружи) → File.
 *
 * Здесь только получение токена; модал открывается вызывающим кодом.
 */
export async function authorizeYandexDisk(): Promise<string> {
  return getYandexDiskAuthToken();
}

export type { YandexDiskPickedFile };
