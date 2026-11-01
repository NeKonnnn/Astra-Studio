import React from 'react';
import ShareSkillDialog from '@/components/ShareSkillDialog';
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

describe('ShareSkillDialog', () => {
  runShareDialogSuite({
    Component: ShareSkillDialog,
    kind: 'skills',
    idProp: 'skillId',
    nameProp: 'skillName',
    title: 'Поделиться skill',
    failShareText: 'Не удалось поделиться skill',
    mockAuth,
    mockSearch,
    mockResolve,
  });
});
