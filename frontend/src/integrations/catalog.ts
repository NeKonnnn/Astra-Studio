import type { IntegrationFeatureKey } from './types';

export type IntegrationId =
  | 'yandex-disk'
  | 'yandex-mail'
  | 'yandex-calendar'
  | 'yandex-meetings'
  | 'google-drive'
  | 'gmail'
  | 'outlook'
  | 'onedrive'
  | 'notion'
  | 'github';

export type IntegrationStatus = 'available' | 'soon';

export interface IntegrationCatalogItem {
  id: IntegrationId;
  label: string;
  description: string;
  group: 'yandex' | 'google' | 'microsoft' | 'other';
  status: IntegrationStatus;
  featureKey: IntegrationFeatureKey;
  configKey:
    | 'yandex_disk'
    | 'yandex_mail'
    | 'yandex_calendar'
    | 'yandex_meetings'
    | 'google_drive'
    | 'gmail'
    | 'onedrive'
    | 'outlook'
    | 'notion'
    | 'github';
}

export const INTEGRATIONS_CATALOG: IntegrationCatalogItem[] = [
  {
    id: 'yandex-disk',
    label: 'Яндекс.Диск',
    description: 'Прикрепить файл с Диска',
    group: 'yandex',
    status: 'available',
    featureKey: 'enable_yandex_disk_integration',
    configKey: 'yandex_disk',
  },
  {
    id: 'yandex-mail',
    label: 'Яндекс Почта',
    description: 'OAuth + контекст почты',
    group: 'yandex',
    status: 'available',
    featureKey: 'enable_yandex_mail_integration',
    configKey: 'yandex_mail',
  },
  {
    id: 'yandex-calendar',
    label: 'Яндекс Календарь',
    description: 'OAuth + CalDAV снимок',
    group: 'yandex',
    status: 'available',
    featureKey: 'enable_yandex_calendar_integration',
    configKey: 'yandex_calendar',
  },
  {
    id: 'yandex-meetings',
    label: 'Яндекс Встречи',
    description: 'OAuth Телемост',
    group: 'yandex',
    status: 'available',
    featureKey: 'enable_yandex_meetings_integration',
    configKey: 'yandex_meetings',
  },
  {
    id: 'google-drive',
    label: 'Google Drive',
    description: 'Picker файлов Google',
    group: 'google',
    status: 'available',
    featureKey: 'enable_google_drive_integration',
    configKey: 'google_drive',
  },
  {
    id: 'gmail',
    label: 'Gmail',
    description: 'Письма из Inbox',
    group: 'google',
    status: 'available',
    featureKey: 'enable_gmail_integration',
    configKey: 'gmail',
  },
  {
    id: 'onedrive',
    label: 'OneDrive',
    description: 'Файлы Microsoft OneDrive',
    group: 'microsoft',
    status: 'available',
    featureKey: 'enable_onedrive_integration',
    configKey: 'onedrive',
  },
  {
    id: 'outlook',
    label: 'Outlook',
    description: 'Почта и календарь Microsoft',
    group: 'microsoft',
    status: 'available',
    featureKey: 'enable_outlook_integration',
    configKey: 'outlook',
  },
  {
    id: 'notion',
    label: 'Notion',
    description: 'Страницы Notion',
    group: 'other',
    status: 'available',
    featureKey: 'enable_notion_integration',
    configKey: 'notion',
  },
  {
    id: 'github',
    label: 'GitHub',
    description: 'Файлы из репозиториев',
    group: 'other',
    status: 'available',
    featureKey: 'enable_github_integration',
    configKey: 'github',
  },
];
