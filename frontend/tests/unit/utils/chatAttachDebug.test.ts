import { logChatAttach, logChatAttachError } from '@/utils/chatAttachDebug';

describe('chatAttachDebug', () => {
  afterEach(() => jest.restoreAllMocks());

  it('logChatAttach пишет JSON со stage и данными', () => {
    const info = jest.spyOn(console, 'info').mockImplementation(() => {});
    logChatAttach('pick', { n: 2 });
    logChatAttach('bare');
    expect(info).toHaveBeenNthCalledWith(1, '[ChatAttach]', '{"stage":"pick","n":2}');
    expect(info).toHaveBeenNthCalledWith(2, '[ChatAttach]', '{"stage":"bare"}');
  });

  it('logChatAttachError: Error -> name/message/первые 4 строки стека', () => {
    const err = jest.spyOn(console, 'error').mockImplementation(() => {});
    const e = new Error('boom');
    e.stack = 'a\nb\nc\nd\ne';
    logChatAttachError('upload', e, { id: 1 });
    const payload = JSON.parse(err.mock.calls[0][1] as string);
    expect(payload).toEqual({
      stage: 'upload',
      error: { name: 'Error', message: 'boom', stack: 'a | b | c | d' },
      id: 1,
    });
  });

  it('logChatAttachError: не-Error приводится к строке', () => {
    const err = jest.spyOn(console, 'error').mockImplementation(() => {});
    logChatAttachError('x', 'text');
    expect(JSON.parse(err.mock.calls[0][1] as string)).toEqual({ stage: 'x', error: { message: 'text' } });
  });
});
