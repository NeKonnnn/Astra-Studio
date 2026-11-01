import { API_ENDPOINTS } from '@/config/api';
import { PLUGINS_API } from '@/plugins/api';
import { MCP_API } from '@/mcp/api';

describe('построители путей API', () => {
  const id = 'srv/один 1';
  const enc = encodeURIComponent(id);

  it('MCP_API кодирует id сервера во всех путях', () => {
    expect(MCP_API.SERVER_STATUS(id)).toBe(`/api/mcp/servers/${enc}/status`);
    expect(MCP_API.SERVER_HEALTH(id)).toBe(`/api/mcp/servers/${enc}/health`);
    expect(MCP_API.SERVER_TOOLS(id)).toBe(`/api/mcp/servers/${enc}/tools`);
    expect(MCP_API.SERVER_VERIFY(id)).toBe(`/api/mcp/servers/${enc}/verify`);
    expect(MCP_API.SERVER_CREDENTIALS(id)).toBe(`/api/mcp/servers/${enc}/credentials`);
  });

  it('API_ENDPOINTS.MCP_* совпадает с MCP_API', () => {
    expect(API_ENDPOINTS.MCP_SERVER_STATUS(id)).toBe(MCP_API.SERVER_STATUS(id));
    expect(API_ENDPOINTS.MCP_SERVER_HEALTH(id)).toBe(MCP_API.SERVER_HEALTH(id));
    expect(API_ENDPOINTS.MCP_SERVER_TOOLS(id)).toBe(MCP_API.SERVER_TOOLS(id));
    expect(API_ENDPOINTS.MCP_SERVER_VERIFY(id)).toBe(MCP_API.SERVER_VERIFY(id));
    expect(API_ENDPOINTS.MCP_SERVER_CREDENTIALS(id)).toBe(MCP_API.SERVER_CREDENTIALS(id));
  });

  it('PLUGINS_API: ITEM/HEALTH/INVOKE кодируют id', () => {
    expect(PLUGINS_API.LIST).toBe('/api/plugins');
    expect(PLUGINS_API.ITEM(id)).toBe(`/api/plugins/${enc}`);
    expect(PLUGINS_API.HEALTH(id)).toBe(`/api/plugins/${enc}/health`);
    expect(PLUGINS_API.INVOKE(id)).toBe(`/api/plugins/${enc}/invoke`);
  });
});
