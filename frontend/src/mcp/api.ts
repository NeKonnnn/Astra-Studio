import { authFetch, getApiUrl } from '../config/api';
import { McpApiError, extractServerIdFromPath, formatMcpApiError } from './errors';
import type { McpPlatformStatus, McpServerConfigPublic, McpToolInfo, McpVerifyResult, McpCredentialsMetadata } from './types';

export const MCP_API = {
  SERVERS: '/api/mcp/servers',
  STATUS: '/api/mcp/status',
  TOOLS: '/api/mcp/tools',
  AGENT_MCP_STATUS: '/api/agent/mcp/status',
  SERVER_STATUS: (id: string) => `/api/mcp/servers/${encodeURIComponent(id)}/status`,
  SERVER_HEALTH: (id: string) => `/api/mcp/servers/${encodeURIComponent(id)}/health`,
  SERVER_TOOLS: (id: string) => `/api/mcp/servers/${encodeURIComponent(id)}/tools`,
  SERVER_VERIFY: (id: string) => `/api/mcp/servers/${encodeURIComponent(id)}/verify`,
  SERVER_CREDENTIALS: (id: string) => `/api/mcp/servers/${encodeURIComponent(id)}/credentials`,
  ATLASSIAN_CONFIG: '/api/mcp/servers/atlassian/config',
  ATLASSIAN_CREDENTIALS: '/api/mcp/servers/atlassian/credentials',
} as const;

async function mcpFetch<T>(endpoint: string, init?: RequestInit): Promise<T> {
  const response = await authFetch(getApiUrl(endpoint), {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers || {}),
    },
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body.detail || body.message || detail;
    } catch {
      /* ignore */
    }
    const serverId = extractServerIdFromPath(endpoint);
    const message = formatMcpApiError(
      response.status,
      typeof detail === 'string' ? detail : 'MCP API error',
      serverId,
    );
    if (response.status === 401) {
      window.dispatchEvent(new CustomEvent('astrachatAuthRequired'));
    }
    throw new McpApiError(message, response.status, serverId);
  }
  return response.json() as Promise<T>;
}

export async function fetchMcpServers(): Promise<McpServerConfigPublic[]> {
  const data = await mcpFetch<{ servers?: McpServerConfigPublic[] }>(MCP_API.SERVERS);
  return data.servers || [];
}

export async function fetchMcpStatus(): Promise<McpPlatformStatus> {
  const data = await mcpFetch<{ mcp_status?: McpPlatformStatus }>(MCP_API.STATUS);
  return data.mcp_status || {};
}

export async function fetchAgentMcpStatus(): Promise<McpPlatformStatus> {
  const data = await mcpFetch<{ mcp_status?: McpPlatformStatus }>(MCP_API.AGENT_MCP_STATUS);
  return data.mcp_status || {};
}

export async function fetchMcpTools(serverId?: string): Promise<McpToolInfo[]> {
  const endpoint = serverId ? MCP_API.SERVER_TOOLS(serverId) : MCP_API.TOOLS;
  const data = await mcpFetch<{ tools?: McpToolInfo[] }>(endpoint);
  return data.tools || [];
}

export async function verifyMcpServer(serverId: string): Promise<McpVerifyResult> {
  const data = await mcpFetch<{ verify?: McpVerifyResult; success?: boolean }>(
    MCP_API.SERVER_VERIFY(serverId),
    { method: 'POST', body: JSON.stringify({}) },
  );
  return data.verify || { server_id: serverId, success: Boolean(data.success) };
}

export async function fetchAtlassianConfig(): Promise<Record<string, unknown> | null> {
  try {
    const data = await mcpFetch<{ config?: Record<string, unknown> }>(MCP_API.ATLASSIAN_CONFIG);
    return data.config || null;
  } catch {
    return null;
  }
}

export async function fetchMcpCredentialsMeta(serverId: string): Promise<McpCredentialsMetadata> {
  try {
    const data = await mcpFetch<McpCredentialsMetadata>(MCP_API.SERVER_CREDENTIALS(serverId));
    return data;
  } catch {
    return { configured: false, storage_available: false };
  }
}

export async function saveMcpCredentials(serverId: string, payload: Record<string, string>): Promise<void> {
  await mcpFetch(MCP_API.SERVER_CREDENTIALS(serverId), {
    method: 'PUT',
    body: JSON.stringify({ payload }),
  });
}

export async function deleteMcpCredentials(serverId: string): Promise<void> {
  await mcpFetch(MCP_API.SERVER_CREDENTIALS(serverId), { method: 'DELETE' });
}

function isSameOriginApiUrl(absoluteUrl: string): boolean {
  try {
    const target = new URL(absoluteUrl);
    const apiBase = new URL(getApiUrl('/'));
    return target.origin === apiBase.origin;
  } catch {
    return false;
  }
}

/** JWT приложения только на свой origin/API; внешние MCP-ссылки — без Authorization. */
export async function downloadMcpFile(fileUrl: string, filename?: string): Promise<void> {
  const token = localStorage.getItem('auth_token') || localStorage.getItem('token');
  const isAbsolute = fileUrl.startsWith('http://') || fileUrl.startsWith('https://');
  const url = isAbsolute
    ? fileUrl
    : getApiUrl(fileUrl.startsWith('/') ? fileUrl : `/${fileUrl}`);

  const sendAuth = Boolean(token) && (!isAbsolute || isSameOriginApiUrl(url));
  const response = await fetch(url, {
    headers: sendAuth ? { Authorization: `Bearer ${token}` } : {},
  });

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = typeof body.detail === 'string' ? body.detail : body.message || detail;
    } catch {
      /* ignore */
    }
    if (response.status === 401) {
      window.dispatchEvent(new CustomEvent('astrachatAuthRequired'));
    }
    throw new Error(detail || 'Не удалось скачать файл');
  }

  const blob = await response.blob();
  if (blob.size < 4) {
    throw new Error('Получен пустой или повреждённый файл');
  }

  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = objectUrl;
  anchor.download = filename || 'download';
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
  URL.revokeObjectURL(objectUrl);
}
