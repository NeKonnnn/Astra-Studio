export type IntegrationFeatureKey =
  | 'enable_yandex_disk_integration'
  | 'enable_yandex_mail_integration'
  | 'enable_yandex_calendar_integration'
  | 'enable_yandex_meetings_integration'
  | 'enable_google_drive_integration'
  | 'enable_gmail_integration'
  | 'enable_onedrive_integration'
  | 'enable_outlook_integration'
  | 'enable_notion_integration'
  | 'enable_github_integration';

export interface ProviderPublicConfig {
  client_id: string;
  api_key?: string;
  redirect_uri: string;
  configured: boolean;
}

export interface IntegrationsPublicConfig {
  features: Partial<Record<IntegrationFeatureKey, boolean>>;
  yandex_disk: ProviderPublicConfig;
  yandex_mail: ProviderPublicConfig;
  yandex_calendar: ProviderPublicConfig;
  yandex_meetings: ProviderPublicConfig;
  google_drive: ProviderPublicConfig;
  gmail: ProviderPublicConfig;
  onedrive: ProviderPublicConfig;
  outlook: ProviderPublicConfig;
  notion: ProviderPublicConfig;
  github: ProviderPublicConfig;
}

export interface PickedBinary {
  name: string;
  blob: Blob;
}

export interface PickedText {
  name: string;
  text: string;
}

export type IntegrationPickResult =
  | { kind: 'file'; file: PickedBinary }
  | { kind: 'text'; item: PickedText };

export interface ListItem {
  id: string;
  title: string;
  subtitle?: string;
  path?: string;
}
