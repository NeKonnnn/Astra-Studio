/**
 * Общий набор тестов диалогов шаринга (ShareAgentDialog / ShareSkillDialog):
 * компоненты отличаются только API-путём и текстами. Моки модулей (jest.mock)
 * объявляются в самих тестовых файлах и передаются через `deps`.
 */
import React from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import '@testing-library/jest-dom';

export type ShareSuiteDeps = {
  Component: React.ComponentType<any>;
  kind: 'agents' | 'skills';
  idProp: 'agentId' | 'skillId';
  nameProp: 'agentName' | 'skillName';
  title: string;
  failShareText: string;
  mockAuth: jest.Mock;
  mockSearch: jest.Mock;
  mockResolve: jest.Mock;
};

function jsonResp(body: unknown, ok = true) {
  return { ok, json: async () => body } as unknown as Response;
}
const badJsonResp = () =>
  ({
    ok: false,
    json: async () => {
      throw new Error('bad json');
    },
  } as unknown as Response);

export function runShareDialogSuite(d: ShareSuiteDeps) {
  const base = `http://api/api/${d.kind}/7`;
  let fetchMock: jest.Mock;
  const onClose = jest.fn();

  const sharesBody = {
    owner: { user_id: 'boss', full_name: 'Иван Петров', permission: 'owner' },
    shares: [
      { user_id: 'anna', full_name: 'Анна Ли', permission: 'viewer' },
      { user_id: 'bob', full_name: null, permission: 'editor' },
      { user_id: 'weird', full_name: '', permission: 'something-else' },
    ],
  };

  function setup(extra: Record<string, unknown> = {}) {
    const props = { open: true, onClose, [d.idProp]: 7, [d.nameProp]: 'Мой объект', isDarkMode: false, ...extra };
    return render(<d.Component {...props} />);
  }
  const input = () => screen.getByPlaceholderText('ФИО, почта или gpbu…') as HTMLInputElement;
  const typeName = (v: string) => fireEvent.change(input(), { target: { value: v } });
  const addBtn = () => screen.getByRole('button', { name: /Добавить/ });
  const flush = async () => {
    await act(async () => {
      await Promise.resolve();
    });
  };
  const findPopover = async () => {
    await waitFor(() => expect(document.querySelector('.MuiPopover-root')).not.toBeNull());
    return document.querySelector('.MuiPopover-root') as HTMLElement;
  };
  const roleChips = () => screen.getAllByText('Зритель', { selector: 'p' });

  beforeEach(() => {
    jest.clearAllMocks();
    d.mockAuth.mockReturnValue({ token: 'tok' });
    fetchMock = jest.fn().mockResolvedValue(jsonResp(sharesBody));
    (global as any).fetch = fetchMock;
    d.mockSearch.mockResolvedValue([]);
    d.mockResolve.mockResolvedValue({ usernames: ['anna'], unresolved: [] });
  });
  afterEach(() => {
    jest.useRealTimers();
  });

  it('загружает список доступа с Bearer-токеном и показывает владельца и получателей', async () => {
    setup();
    expect(await screen.findByText('Иван Петров')).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(`${base}/shares`, {
      headers: { 'Content-Type': 'application/json', Authorization: 'Bearer tok' },
    });
    expect(screen.getByText('Владелец')).toBeInTheDocument();
    expect(screen.getByText('Анна Ли')).toBeInTheDocument();
    expect(screen.getByText('АЛ')).toBeInTheDocument(); // инициалы «Анна Ли»
    expect(screen.getByText('BO')).toBeInTheDocument(); // без ФИО: первые 2 буквы логина
    expect(screen.getByText('Редактор', { selector: 'p' })).toBeTruthy();
    expect(screen.getByText('Мой объект')).toBeInTheDocument();
    expect(screen.getByText(d.title)).toBeInTheDocument();
  });

  it('неизвестная роль получателя отображается как «Зритель»', async () => {
    setup();
    await screen.findByText('Иван Петров');
    expect(roleChips().length).toBe(3); // триггер добавления + anna (viewer) + weird (fallback)
  });

  it('пустой список получателей и не-массив: подсказка «Пока ни с кем не поделились»', async () => {
    fetchMock.mockResolvedValue(jsonResp({ owner: null, shares: 'not-array' }));
    setup();
    expect(await screen.findByText('Пока ни с кем не поделились')).toBeInTheDocument();
    expect(screen.queryByText('Владелец')).toBeNull();
  });

  it('пустой ответ (null) не ломает диалог', async () => {
    fetchMock.mockResolvedValue(jsonResp(null));
    setup();
    expect(await screen.findByText('Пока ни с кем не поделились')).toBeInTheDocument();
  });

  it('без токена список не грузится', async () => {
    d.mockAuth.mockReturnValue({ token: null });
    setup();
    await flush();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('при id=0 список не грузится', async () => {
    setup({ [d.idProp]: 0 });
    await flush();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('закрытый диалог ничего не грузит', async () => {
    setup({ open: false });
    await flush();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('ошибка загрузки списка: показывает detail сервера', async () => {
    fetchMock.mockResolvedValue(jsonResp({ detail: 'Нет прав на просмотр' }, false));
    setup();
    expect(await screen.findByText('Нет прав на просмотр')).toBeInTheDocument();
  });

  it('ошибка загрузки с невалидным JSON: текст по умолчанию', async () => {
    fetchMock.mockResolvedValue(badJsonResp());
    setup();
    expect(await screen.findByText('Не удалось загрузить список доступа')).toBeInTheDocument();
  });

  it('сетевая ошибка при загрузке: message ошибки; не-Error -> «Ошибка загрузки»', async () => {
    fetchMock.mockRejectedValueOnce(new Error('network down'));
    const { unmount } = setup();
    expect(await screen.findByText('network down')).toBeInTheDocument();
    unmount();
    fetchMock.mockRejectedValueOnce('str');
    setup();
    expect(await screen.findByText('Ошибка загрузки')).toBeInTheDocument();
  });

  it('кнопка «Добавить» заблокирована при пустом поле, Enter с пустым полем показывает ошибку', async () => {
    setup();
    await screen.findByText('Иван Петров');
    expect(addBtn()).toBeDisabled();
    fireEvent.keyDown(input(), { key: 'Enter' });
    expect(await screen.findByText('Укажите ФИО, почту или gpbu')).toBeInTheDocument();
    expect(d.mockResolve).not.toHaveBeenCalled();
  });

  it('Enter без токена: «Нет авторизации», сеть не вызывается', async () => {
    d.mockAuth.mockReturnValue({ token: null });
    setup();
    typeName('anna');
    fireEvent.keyDown(input(), { key: 'Enter' });
    expect(await screen.findByText('Нет авторизации')).toBeInTheDocument();
    expect(d.mockResolve).not.toHaveBeenCalled();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('выдача доступа: резолвит логины, POST с usernames и ролью viewer по умолчанию, обновляет список', async () => {
    setup();
    await screen.findByText('Иван Петров');
    fetchMock.mockClear();
    fetchMock.mockResolvedValueOnce(jsonResp({ success: true, message: 'Выдано anna' }));
    fetchMock.mockResolvedValueOnce(jsonResp(sharesBody));
    typeName('Анна');
    fireEvent.click(addBtn());
    expect(await screen.findByText('Выдано anna')).toBeInTheDocument();
    expect(d.mockResolve).toHaveBeenCalledWith('tok', 'Анна');
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`${base}/share`);
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body)).toEqual({ usernames: ['anna'], permission: 'viewer' });
    expect(fetchMock.mock.calls[1][0]).toBe(`${base}/shares`);
    expect(input().value).toBe('');
  });

  it('Enter в поле запускает выдачу доступа', async () => {
    setup();
    await screen.findByText('Иван Петров');
    fetchMock.mockClear();
    fetchMock.mockResolvedValue(jsonResp({ success: true, message: 'ok' }));
    typeName('anna');
    fireEvent.keyDown(input(), { key: 'Enter' });
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(fetchMock.mock.calls[0][0]).toBe(`${base}/share`);
  });

  it('выбор роли «Редактор» в выпадающем списке уходит на сервер как editor', async () => {
    setup();
    await screen.findByText('Иван Петров');
    expect(screen.getAllByText(/не может менять настройки/).length).toBeGreaterThan(0);
    // триггер выбора роли при добавлении: «Зритель» вне списка участников (рядом с полем)
    // первый «Зритель» в DOM - триггер выбора роли при добавлении (идёт раньше списка участников)
    fireEvent.click(roleChips()[0]);
    const pop = await findPopover();
    fireEvent.click(within(pop).getByText('Редактор'));
    await waitFor(() => expect(screen.getAllByText(/изменять/).length).toBeGreaterThan(0));
    fetchMock.mockClear();
    fetchMock.mockResolvedValue(jsonResp({ success: true, message: 'ok' }));
    typeName('anna');
    fireEvent.click(addBtn());
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ usernames: ['anna'], permission: 'editor' });
  });

  it('закрытие меню выбора роли при добавлении (Escape) не меняет роль', async () => {
    setup();
    await screen.findByText('Иван Петров');
    fireEvent.click(roleChips()[0]);
    const pop = await findPopover();
    fireEvent.keyDown(pop, { key: 'Escape' });
    fetchMock.mockClear();
    fetchMock.mockResolvedValue(jsonResp({ success: true, message: 'ok' }));
    typeName('anna');
    fireEvent.click(addBtn());
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(JSON.parse(fetchMock.mock.calls[0][1].body).permission).toBe('viewer');
  });

  it('не найденные логины: ошибка «Не найдены: …», POST не отправляется', async () => {
    d.mockResolve.mockResolvedValue({ usernames: ['anna'], unresolved: ['ghost', 'nobody'] });
    setup();
    await screen.findByText('Иван Петров');
    fetchMock.mockClear();
    typeName('anna, ghost, nobody');
    fireEvent.click(addBtn());
    expect(await screen.findByText('Не найдены: ghost, nobody')).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('резолвер вернул пустой список логинов: ошибка ввода, POST не отправляется', async () => {
    d.mockResolve.mockResolvedValue({ usernames: [], unresolved: [] });
    setup();
    await screen.findByText('Иван Петров');
    fetchMock.mockClear();
    typeName(' , ');
    fireEvent.click(addBtn());
    expect(await screen.findByText('Укажите ФИО, почту или gpbu')).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('ошибка API при выдаче доступа: detail сервера, поле ввода не очищается', async () => {
    setup();
    await screen.findByText('Иван Петров');
    fetchMock.mockResolvedValueOnce(jsonResp({ detail: 'Нельзя поделиться с собой' }, false));
    typeName('boss');
    fireEvent.click(addBtn());
    expect(await screen.findByText('Нельзя поделиться с собой')).toBeInTheDocument();
    expect(input().value).toBe('boss');
  });

  it('ошибка API без detail: текст по умолчанию', async () => {
    setup();
    await screen.findByText('Иван Петров');
    fetchMock.mockResolvedValueOnce(badJsonResp());
    typeName('anna');
    fireEvent.click(addBtn());
    expect(await screen.findByText(d.failShareText)).toBeInTheDocument();
  });

  it('не-Error исключение при выдаче доступа: «Ошибка»', async () => {
    d.mockResolve.mockRejectedValue('boom');
    setup();
    await screen.findByText('Иван Петров');
    typeName('anna');
    fireEvent.click(addBtn());
    expect(await screen.findByText('Ошибка')).toBeInTheDocument();
  });

  it('Error при резолве логинов показывает его сообщение', async () => {
    d.mockResolve.mockRejectedValue(new Error('Каталог недоступен'));
    setup();
    await screen.findByText('Иван Петров');
    typeName('anna');
    fireEvent.click(addBtn());
    expect(await screen.findByText('Каталог недоступен')).toBeInTheDocument();
  });

  it('предупреждения сервера показываются как warning, alert-ы закрываются', async () => {
    setup();
    await screen.findByText('Иван Петров');
    fetchMock.mockResolvedValueOnce(
      jsonResp({ success: true, message: 'Готово', warnings: ['Роль понижена до viewer'] }),
    );
    typeName('anna');
    fireEvent.click(addBtn());
    expect(await screen.findByText('Готово')).toBeInTheDocument();
    expect(screen.getByText('Роль понижена до viewer')).toBeInTheDocument();
    screen.getAllByTitle('Close').forEach((b) => fireEvent.click(b));
    await waitFor(() => expect(screen.queryByText('Готово')).toBeNull());
    expect(screen.queryByText('Роль понижена до viewer')).toBeNull();
  });

  it('закрытие alert с ошибкой скрывает её', async () => {
    fetchMock.mockResolvedValue(jsonResp({ detail: 'Ошибка X' }, false));
    setup();
    const err = await screen.findByText('Ошибка X');
    fireEvent.click(screen.getByTitle('Close'));
    await waitFor(() => expect(err).not.toBeInTheDocument());
  });

  it('смена роли получателя: POST только с этим user_id и новой ролью, затем перезагрузка', async () => {
    setup();
    await screen.findByText('Анна Ли');
    fetchMock.mockClear();
    fetchMock.mockResolvedValueOnce(jsonResp({ success: true }));
    fetchMock.mockResolvedValueOnce(jsonResp(sharesBody));
    fireEvent.click(roleChips()[roleChips().length - 2]); // anna
    const pop = await findPopover();
    fireEvent.click(within(pop).getByText('Редактор'));
    expect(await screen.findByText('Доступ выдан')).toBeInTheDocument();
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`${base}/share`);
    expect(JSON.parse(init.body)).toEqual({ usernames: ['anna'], permission: 'editor' });
    expect(fetchMock.mock.calls[1][0]).toBe(`${base}/shares`);
  });

  it('смена роли: сообщение сервера отображается', async () => {
    setup();
    await screen.findByText('Анна Ли');
    fetchMock.mockResolvedValueOnce(jsonResp({ success: true, message: 'Роль изменена' }));
    fireEvent.click(roleChips()[roleChips().length - 2]);
    const pop = await findPopover();
    fireEvent.click(within(pop).getByText('Зритель'));
    expect(await screen.findByText('Роль изменена')).toBeInTheDocument();
  });

  it('смена роли: ошибка API показывается', async () => {
    setup();
    await screen.findByText('Анна Ли');
    fetchMock.mockResolvedValueOnce(jsonResp({ detail: 'Недостаточно прав' }, false));
    fireEvent.click(roleChips()[roleChips().length - 2]);
    const pop = await findPopover();
    fireEvent.click(within(pop).getByText('Редактор'));
    expect(await screen.findByText('Недостаточно прав')).toBeInTheDocument();
  });

  it('смена роли: не-Error исключение -> «Ошибка»', async () => {
    setup();
    await screen.findByText('Анна Ли');
    fetchMock.mockRejectedValueOnce('x');
    fireEvent.click(roleChips()[roleChips().length - 2]);
    const pop = await findPopover();
    fireEvent.click(within(pop).getByText('Редактор'));
    expect(await screen.findByText('Ошибка')).toBeInTheDocument();
  });

  it('закрытие меню роли получателя без выбора не отправляет запросов', async () => {
    setup();
    await screen.findByText('Анна Ли');
    fetchMock.mockClear();
    fireEvent.click(roleChips()[roleChips().length - 2]);
    const pop = await findPopover();
    fireEvent.keyDown(pop, { key: 'Escape' });
    await flush();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('отзыв доступа: DELETE с url-кодированным user_id, сообщение сервера и перезагрузка списка', async () => {
    fetchMock.mockResolvedValue(
      jsonResp({
        owner: sharesBody.owner,
        shares: [{ user_id: 'a b/c', full_name: 'Спец Символ', permission: 'viewer' }],
      }),
    );
    setup();
    await screen.findByText('Спец Символ');
    fetchMock.mockClear();
    fetchMock.mockResolvedValueOnce(jsonResp({ message: 'Доступ отозван у a b/c' }));
    fetchMock.mockResolvedValueOnce(jsonResp({ owner: sharesBody.owner, shares: [] }));
    fireEvent.click(screen.getByTitle('Отозвать доступ'));
    expect(await screen.findByText('Доступ отозван у a b/c')).toBeInTheDocument();
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`${base}/share/${encodeURIComponent('a b/c')}`);
    expect(init.method).toBe('DELETE');
    expect(await screen.findByText('Пока ни с кем не поделились')).toBeInTheDocument();
  });

  it('отзыв доступа без message: «Доступ снят»', async () => {
    setup();
    await screen.findByText('Анна Ли');
    fetchMock.mockResolvedValueOnce(jsonResp({}));
    fireEvent.click(screen.getAllByTitle('Отозвать доступ')[0]);
    expect(await screen.findByText('Доступ снят')).toBeInTheDocument();
  });

  it('отзыв доступа: ошибка API (detail) и дефолтный текст', async () => {
    setup();
    await screen.findByText('Анна Ли');
    fetchMock.mockResolvedValueOnce(jsonResp({ detail: 'Только владелец' }, false));
    fireEvent.click(screen.getAllByTitle('Отозвать доступ')[0]);
    expect(await screen.findByText('Только владелец')).toBeInTheDocument();
    fetchMock.mockResolvedValueOnce(badJsonResp());
    fireEvent.click(screen.getAllByTitle('Отозвать доступ')[0]);
    expect(await screen.findByText('Не удалось снять доступ')).toBeInTheDocument();
  });

  it('отзыв доступа: не-Error исключение -> «Ошибка»', async () => {
    setup();
    await screen.findByText('Анна Ли');
    fetchMock.mockRejectedValueOnce('x');
    fireEvent.click(screen.getAllByTitle('Отозвать доступ')[0]);
    expect(await screen.findByText('Ошибка')).toBeInTheDocument();
  });

  it('кнопки отзыва есть только у получателей, у владельца нет', async () => {
    setup();
    await screen.findByText('Анна Ли');
    expect(screen.getAllByTitle('Отозвать доступ')).toHaveLength(3);
  });

  it('во время сохранения кнопки заблокированы, клик по чипу роли не открывает меню', async () => {
    setup();
    await screen.findByText('Анна Ли');
    let resolveDelete: (v: Response) => void = () => {};
    fetchMock.mockImplementationOnce(
      () =>
        new Promise<Response>((r) => {
          resolveDelete = r;
        }),
    );
    fireEvent.click(screen.getAllByTitle('Отозвать доступ')[0]);
    await waitFor(() => expect(screen.getAllByTitle('Отозвать доступ')[1]).toBeDisabled());
    expect(addBtn()).toBeDisabled();
    fireEvent.click(roleChips()[roleChips().length - 2]);
    expect(document.querySelector('.MuiPopover-root')).toBeNull();
    fetchMock.mockResolvedValueOnce(jsonResp(sharesBody));
    await act(async () => {
      resolveDelete(jsonResp({ message: 'ok' }));
    });
  });

  it('поиск пользователей: дебаунс 400 мс, последний фрагмент после запятой, лимит 10 и signal', async () => {
    jest.useFakeTimers();
    d.mockSearch.mockResolvedValue([
      { user_id: 'u1', username: 'anna', full_name: 'Анна Ли', email: 'anna@x.ru' },
      { user_id: 'u2', username: '', full_name: null, email: null },
    ]);
    setup();
    await flush();
    typeName('bob, an');
    typeName('bob, ann');
    expect(d.mockSearch).not.toHaveBeenCalled();
    await act(async () => {
      jest.advanceTimersByTime(399);
    });
    expect(d.mockSearch).not.toHaveBeenCalled();
    await act(async () => {
      jest.advanceTimersByTime(2);
    });
    expect(d.mockSearch).toHaveBeenCalledTimes(1);
    const args = d.mockSearch.mock.calls[0];
    expect(args[0]).toBe('tok');
    expect(args[1]).toBe('ann');
    expect(args[2]).toBe(10);
    expect(args[3]).toBeInstanceOf(AbortSignal);
    expect(args[4]).toBe(true);
    expect(await screen.findByText('anna · anna@x.ru')).toBeInTheDocument();
  });

  it('выбор подсказки подставляет логин (username)', async () => {
    jest.useFakeTimers();
    d.mockSearch.mockResolvedValue([
      { user_id: 'u1', username: 'novik', full_name: 'Сергей Новик', email: 'a@x.ru' },
    ]);
    setup();
    await flush();
    typeName('се');
    await act(async () => {
      jest.advanceTimersByTime(450);
    });
    fireEvent.click(await screen.findByText('Сергей Новик', { selector: 'p' }));
    expect(input().value).toBe('novik');
  });

  it('выбор подсказки без username использует user_id', async () => {
    jest.useFakeTimers();
    d.mockSearch.mockResolvedValue([{ user_id: 'u9', username: '', full_name: 'Без Логина' }]);
    setup();
    await flush();
    typeName('бе');
    await act(async () => {
      jest.advanceTimersByTime(450);
    });
    fireEvent.click(await screen.findByText('Без Логина', { selector: 'p' }));
    expect(input().value).toBe('u9');
  });

  it('короткий запрос (< 2 символов) не вызывает поиск', async () => {
    jest.useFakeTimers();
    setup();
    await flush();
    typeName('a');
    await act(async () => {
      jest.advanceTimersByTime(1000);
    });
    expect(d.mockSearch).not.toHaveBeenCalled();
  });

  it('поиск без токена не выполняется', async () => {
    jest.useFakeTimers();
    d.mockAuth.mockReturnValue({ token: null });
    setup();
    typeName('anna');
    await act(async () => {
      jest.advanceTimersByTime(1000);
    });
    expect(d.mockSearch).not.toHaveBeenCalled();
  });

  it('ошибка поиска молча сбрасывает подсказки и не показывает ошибку', async () => {
    jest.useFakeTimers();
    d.mockSearch.mockRejectedValue(new Error('search down'));
    setup();
    await flush();
    typeName('anna');
    await act(async () => {
      jest.advanceTimersByTime(450);
    });
    await flush();
    expect(d.mockSearch).toHaveBeenCalled();
    expect(screen.queryByText('search down')).toBeNull();
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('устаревший (отменённый) поиск не применяет результат', async () => {
    jest.useFakeTimers();
    let resolveFirst: (v: any) => void = () => {};
    d.mockSearch.mockImplementationOnce(
      () =>
        new Promise((r) => {
          resolveFirst = r;
        }),
    );
    setup();
    await flush();
    typeName('anna');
    await act(async () => {
      jest.advanceTimersByTime(450);
    });
    typeName('a'); // abort предыдущего запроса
    await act(async () => {
      resolveFirst([{ user_id: 'u1', username: 'stale', full_name: 'Устаревший' }]);
    });
    expect(screen.queryByText('Устаревший')).toBeNull();
  });

  it('отменённый поиск, завершившийся ошибкой, ничего не меняет', async () => {
    jest.useFakeTimers();
    let rejectFirst: (e: any) => void = () => {};
    d.mockSearch.mockImplementationOnce(
      () =>
        new Promise((_, rej) => {
          rejectFirst = rej;
        }),
    );
    setup();
    await flush();
    typeName('anna');
    await act(async () => {
      jest.advanceTimersByTime(450);
    });
    typeName('a');
    await act(async () => {
      rejectFirst(new Error('aborted'));
    });
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('размонтирование во время дебаунса отменяет таймер поиска', async () => {
    jest.useFakeTimers();
    const { unmount } = setup();
    await flush();
    typeName('anna');
    unmount();
    await act(async () => {
      jest.advanceTimersByTime(1000);
    });
    expect(d.mockSearch).not.toHaveBeenCalled();
  });

  it('при повторном открытии диалога поле сбрасывается и список перезагружается', async () => {
    const idName = { [d.idProp]: 7, [d.nameProp]: 'X' };
    const { rerender } = setup();
    await screen.findByText('Анна Ли');
    typeName('abc');
    rerender(<d.Component open={false} onClose={onClose} {...idName} isDarkMode={false} />);
    fetchMock.mockClear();
    rerender(<d.Component open onClose={onClose} {...idName} isDarkMode />);
    await screen.findByText('Анна Ли');
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(input().value).toBe('');
  });

  it('кнопки «Закрыть» и крестик вызывают onClose', async () => {
    setup();
    await screen.findByText('Анна Ли');
    fireEvent.click(screen.getByRole('button', { name: 'Закрыть' }));
    expect(onClose).toHaveBeenCalledTimes(1);
    const dlg = screen.getByRole('dialog');
    const xBtn = dlg.querySelector('[data-testid="CloseIcon"]')!.closest('button') as HTMLElement;
    fireEvent.click(xBtn);
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it('тёмная тема рендерится', async () => {
    setup({ isDarkMode: true });
    expect(await screen.findByText('Анна Ли')).toBeInTheDocument();
  });

  it('инициалы: пустые ФИО и user_id дают «?»', async () => {
    fetchMock.mockResolvedValue(
      jsonResp({ owner: { user_id: '', full_name: '', permission: 'owner' }, shares: [] }),
    );
    setup();
    expect(await screen.findByText('?')).toBeInTheDocument();
  });
}
