import { fetchIntegrationsConfig } from '../config';

declare global {
  interface Window {
    gapi?: any;
    google?: any;
  }
}

let apiKey = '';
let clientId = '';
let oauthToken: string | null = null;
let initialized = false;

const SCOPE = [
  'https://www.googleapis.com/auth/drive.readonly',
  'https://www.googleapis.com/auth/drive.file',
];

async function loadScript(src: string): Promise<void> {
  if (document.querySelector(`script[src="${src}"]`)) return;
  await new Promise<void>((resolve, reject) => {
    const s = document.createElement('script');
    s.src = src;
    s.onload = () => resolve();
    s.onerror = () => reject(new Error(`Failed to load ${src}`));
    document.body.appendChild(s);
  });
}

async function initialize(): Promise<void> {
  if (initialized) return;
  const cfg = await fetchIntegrationsConfig();
  apiKey = cfg.google_drive.api_key || '';
  clientId = cfg.google_drive.client_id || '';
  if (!apiKey || !clientId) {
    throw new Error('Google Drive: задайте GOOGLE_CLIENT_ID и GOOGLE_API_KEY');
  }
  await loadScript('https://apis.google.com/js/api.js');
  await loadScript('https://accounts.google.com/gsi/client');
  await new Promise<void>((resolve) => {
    window.gapi.load('picker', () => resolve());
  });
  initialized = true;
}

async function getAuthToken(): Promise<string> {
  if (oauthToken) return oauthToken;
  return new Promise((resolve, reject) => {
    const tokenClient = window.google.accounts.oauth2.initTokenClient({
      client_id: clientId,
      scope: SCOPE.join(' '),
      callback: (response: any) => {
        if (response.access_token) {
          oauthToken = response.access_token;
          resolve(oauthToken!);
        } else reject(new Error('Google OAuth: нет access_token'));
      },
      error_callback: (error: any) =>
        reject(new Error(error?.message || 'Google OAuth error')),
    });
    tokenClient.requestAccessToken();
  });
}

export async function pickGoogleDriveFile(): Promise<{ name: string; blob: Blob } | null> {
  await initialize();
  const token = await getAuthToken();

  return new Promise((resolve, reject) => {
    const picker = new window.google.picker.PickerBuilder()
      .enableFeature(window.google.picker.Feature.NAV_HIDDEN)
      .addView(
        new window.google.picker.DocsView()
          .setIncludeFolders(false)
          .setSelectFolderEnabled(false),
      )
      .setOAuthToken(token)
      .setDeveloperKey(apiKey)
      .setCallback(async (data: any) => {
        if (data[window.google.picker.Response.ACTION] === window.google.picker.Action.CANCEL) {
          resolve(null);
          return;
        }
        if (data[window.google.picker.Response.ACTION] !== window.google.picker.Action.PICKED) {
          return;
        }
        try {
          const doc = data[window.google.picker.Response.DOCUMENTS][0];
          const fileId = doc[window.google.picker.Document.ID];
          const fileName = doc[window.google.picker.Document.NAME];
          const mimeType = doc[window.google.picker.Document.MIME_TYPE] || '';
          let downloadUrl: string;
          if (String(mimeType).includes('google-apps')) {
            let exportFormat = 'application/pdf';
            if (mimeType.includes('document')) exportFormat = 'text/plain';
            else if (mimeType.includes('spreadsheet')) exportFormat = 'text/csv';
            downloadUrl = `https://www.googleapis.com/drive/v3/files/${fileId}/export?mimeType=${encodeURIComponent(exportFormat)}`;
          } else {
            downloadUrl = `https://www.googleapis.com/drive/v3/files/${fileId}?alt=media`;
          }
          const resp = await fetch(downloadUrl, {
            headers: { Authorization: `Bearer ${token}` },
          });
          if (!resp.ok) throw new Error(`Download failed (${resp.status})`);
          const blob = await resp.blob();
          resolve({ name: fileName, blob });
        } catch (err) {
          reject(err);
        }
      })
      .build();
    picker.setVisible(true);
  });
}
