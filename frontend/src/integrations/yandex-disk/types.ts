export type { IntegrationsPublicConfig, ProviderPublicConfig as YandexDiskPublicConfig } from '../types';

export interface YandexDiskResource {
  name: string;
  path: string;
  type: 'file' | 'dir' | string;
  mime_type?: string;
  size?: number;
  modified?: string;
  preview?: string;
}

export interface YandexDiskListResult {
  path: string;
  name: string;
  items: YandexDiskResource[];
}

export interface YandexDiskPickedFile {
  name: string;
  path: string;
  blob: Blob;
}
