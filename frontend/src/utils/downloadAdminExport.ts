import { authFetch, getApiUrl, API_ENDPOINTS } from '../config/api';

export type AdminExportFormat = 'xlsx' | 'csv' | 'json';

export type AdminExportOptions = {
  days?: number;
  entityType?: string;
  format?: AdminExportFormat;
};

/** Скачать admin-статистику (xlsx / csv / json). */
export async function downloadAdminExport(
  options: AdminExportOptions = {},
): Promise<void> {
  const days = Math.max(1, Math.min(options.days ?? 14, 90));
  const format: AdminExportFormat = options.format ?? 'xlsx';
  const qs = new URLSearchParams({
    days: String(days),
    format,
  });
  if (options.entityType) {
    qs.set('entity_type', options.entityType);
  }

  const resp = await authFetch(
    getApiUrl(`${API_ENDPOINTS.ADMIN_EXPORT}?${qs.toString()}`),
  );
  if (!resp.ok) {
    const text = await resp.text().catch(() => '');
    throw new Error(text || `Export failed (${resp.status})`);
  }

  const blob = await resp.blob();
  const url = URL.createObjectURL(blob);
  try {
    const a = document.createElement('a');
    a.href = url;
    a.download = `admin_export_${days}d.${format === 'xlsx' ? 'xlsx' : format}`;
    document.body.appendChild(a);
    a.click();
    a.remove();
  } finally {
    URL.revokeObjectURL(url);
  }
}
