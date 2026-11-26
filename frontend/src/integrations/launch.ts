import type { IntegrationId } from './catalog';
import type { IntegrationPickResult, ListItem } from './types';
import { pickGoogleDriveFile } from './google-drive/picker';
import {
  fetchGmailMessageText,
  getGmailToken,
  listGmailMessages,
} from './gmail/client';
import {
  downloadOneDriveFile,
  getOneDriveToken,
  listOneDriveFiles,
} from './onedrive/client';
import {
  fetchOutlookEventText,
  fetchOutlookMessageText,
  getOutlookToken,
  listOutlookEvents,
  listOutlookMessages,
} from './outlook/client';
import {
  fetchGitHubFileText,
  getGitHubToken,
  listGitHubFiles,
  listGitHubRepos,
} from './github/client';
import {
  fetchNotionPageText,
  getNotionToken,
  listNotionPages,
} from './notion/client';
import {
  fetchYandexCalendarText,
  fetchYandexMailText,
  fetchYandexMeetingsText,
  getYandexCalendarToken,
  getYandexMailToken,
  getYandexMeetingsToken,
  listYandexCalendarItems,
  listYandexMailItems,
  listYandexMeetingsItems,
} from './yandex/client';
import { authorizeYandexDisk } from './yandex-disk';

export type ListSession = {
  title: string;
  token: string;
  items: ListItem[];
  loadText: (item: ListItem) => Promise<{ name: string; text: string }>;
  /** Для GitHub: после выбора репо открываем файлы */
  onItem?: (item: ListItem) => Promise<ListSession | IntegrationPickResult | null>;
};

export type LaunchOutcome =
  | { type: 'file'; name: string; blob: Blob }
  | { type: 'yandex-disk'; token: string }
  | { type: 'list'; session: ListSession };

export async function launchIntegration(id: IntegrationId): Promise<LaunchOutcome> {
  switch (id) {
    case 'yandex-disk': {
      const token = await authorizeYandexDisk();
      return { type: 'yandex-disk', token };
    }
    case 'google-drive': {
      const picked = await pickGoogleDriveFile();
      if (!picked) throw new Error('Файл не выбран');
      return { type: 'file', name: picked.name, blob: picked.blob };
    }
    case 'gmail': {
      const token = await getGmailToken();
      const items = await listGmailMessages(token);
      return {
        type: 'list',
        session: {
          title: 'Gmail',
          token,
          items,
          loadText: (item) => fetchGmailMessageText(token, item.id),
        },
      };
    }
    case 'onedrive': {
      const token = await getOneDriveToken();
      const items = await listOneDriveFiles(token);
      return {
        type: 'list',
        session: {
          title: 'OneDrive',
          token,
          items,
          loadText: async () => ({ name: '', text: '' }),
          onItem: async (item) => {
            const file = await downloadOneDriveFile(token, item.id, item.title);
            return { kind: 'file', file };
          },
        },
      };
    }
    case 'outlook': {
      const token = await getOutlookToken();
      const [messages, events] = await Promise.all([
        listOutlookMessages(token),
        listOutlookEvents(token).catch(() => [] as ListItem[]),
      ]);
      const items = [
        ...messages.map((m) => ({ ...m, subtitle: `✉ ${m.subtitle || ''}` })),
        ...events.map((e) => ({ ...e, id: `event:${e.id}`, subtitle: `📅 ${e.subtitle || ''}` })),
      ];
      return {
        type: 'list',
        session: {
          title: 'Outlook',
          token,
          items,
          loadText: (item) =>
            item.id.startsWith('event:')
              ? fetchOutlookEventText(token, item.id.slice(6))
              : fetchOutlookMessageText(token, item.id),
        },
      };
    }
    case 'github': {
      const token = await getGitHubToken();
      const repos = await listGitHubRepos(token);
      return {
        type: 'list',
        session: {
          title: 'GitHub · репозитории',
          token,
          items: repos,
          loadText: async () => ({ name: '', text: '' }),
          onItem: async (repo) => {
            const files = await listGitHubFiles(token, repo.id);
            const fileItems = files.filter((f) => f.subtitle === 'file');
            return {
              title: `GitHub · ${repo.id}`,
              token,
              items: fileItems.length ? fileItems : files,
              loadText: (item) =>
                fetchGitHubFileText(token, repo.id, item.path || item.title),
            };
          },
        },
      };
    }
    case 'notion': {
      const token = await getNotionToken();
      const items = await listNotionPages(token);
      return {
        type: 'list',
        session: {
          title: 'Notion',
          token,
          items,
          loadText: (item) => fetchNotionPageText(token, item.id, item.title),
        },
      };
    }
    case 'yandex-mail': {
      const token = await getYandexMailToken();
      const items = await listYandexMailItems(token);
      return {
        type: 'list',
        session: {
          title: 'Яндекс Почта',
          token,
          items,
          loadText: (item) => fetchYandexMailText(token, item.id),
        },
      };
    }
    case 'yandex-calendar': {
      const token = await getYandexCalendarToken();
      const items = await listYandexCalendarItems(token);
      return {
        type: 'list',
        session: {
          title: 'Яндекс Календарь',
          token,
          items,
          loadText: (item) => fetchYandexCalendarText(token, item.id),
        },
      };
    }
    case 'yandex-meetings': {
      const token = await getYandexMeetingsToken();
      const items = await listYandexMeetingsItems(token);
      return {
        type: 'list',
        session: {
          title: 'Яндекс Встречи',
          token,
          items,
          loadText: (item) => fetchYandexMeetingsText(token, item.id),
        },
      };
    }
    default:
      throw new Error(`Интеграция ${id} ещё не реализована`);
  }
}
