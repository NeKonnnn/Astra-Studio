/**
 * @jest-environment jsdom
 */
import { clearActiveAgent } from '@/utils/clearActiveAgent';

jest.mock('@/utils/applyAgentMcp', () => ({
  persistAgentMcpConfig: jest.fn(),
}));
jest.mock('@/utils/agentArtifactsEnabled', () => ({
  clearAgentArtifactsEnabled: jest.fn(),
}));

describe('clearActiveAgent', () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem('active_agent_id', '42');
    localStorage.setItem('active_agent_name', 'Demo');
    localStorage.setItem('active_agent_prompt', 'hi');
  });

  it('removes agent keys and dispatches agentSelected(null)', () => {
    const spy = jest.fn();
    window.addEventListener('agentSelected', spy);
    clearActiveAgent();
    expect(localStorage.getItem('active_agent_id')).toBeNull();
    expect(localStorage.getItem('active_agent_name')).toBeNull();
    expect(localStorage.getItem('active_agent_prompt')).toBeNull();
    expect(spy).toHaveBeenCalled();
    expect((spy.mock.calls[0][0] as CustomEvent).detail).toBeNull();
    window.removeEventListener('agentSelected', spy);
  });
});
