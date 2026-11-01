import React from 'react';
import ShareAgentDialog from '@/components/ShareAgentDialog';
import { runShareDialogSuite } from '@/components/__test_support__/shareDialogSuite';

const mockAuth = jest.fn();
const mockSearch = jest.fn();
const mockResolve = jest.fn();

jest.mock('@/contexts/AuthContext', () => ({ useAuth: () => mockAuth() }));
jest.mock('@/config/api', () => ({
  authFetch: (...a: unknown[]) => (require('@tests/unit/helpers/authFetchMock').authFetchMock as (...x: unknown[]) => unknown)(...a), getApiUrl: (p: string) => `http://api${p}` }));
jest.mock('@/utils/directoryUserSearch', () => ({
  ...jest.requireActual('../utils/directoryUserSearch'),
  searchDirectoryUsers: (...a: unknown[]) => mockSearch(...a),
  resolveShareUsernames: (...a: unknown[]) => mockResolve(...a),
}));

describe('ShareAgentDialog', () => {
  runShareDialogSuite({
    Component: ShareAgentDialog,
    kind: 'agents',
    idProp: 'agentId',
    nameProp: 'agentName',
    title: 'Поделиться агентом',
    failShareText: 'Не удалось поделиться агентом',
    mockAuth,
    mockSearch,
    mockResolve,
  });
});
