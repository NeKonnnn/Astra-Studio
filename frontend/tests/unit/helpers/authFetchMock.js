/**
 * Общий мок `authFetch` для тестов, которые подменяют '@/config/api' фабрикой.
 * Реальный authFetch добавляет Authorization и делегирует в global.fetch;
 * в тестах делаем то же самое, но с заголовками из (замоканного) getAuthFetchHeaders
 * либо из localStorage.auth_token, как в настоящем getAuthFetchHeaders.
 *
 * Использование внутри jest.mock-фабрики:
 *   authFetch: (...a) => require('@tests/unit/helpers/authFetchMock').authFetchMock(...a),
 */
function defaultHeaders() {
  let token = null;
  try {
    token = typeof localStorage !== 'undefined' ? localStorage.getItem('auth_token') : null;
  } catch (e) {
    token = null;
  }
  return token ? { Authorization: `Bearer ${token}` } : {};
}

function currentHeaders() {
  // eslint-disable-next-line global-require
  const api = require('@/config/api');
  return typeof api.getAuthFetchHeaders === 'function' ? api.getAuthFetchHeaders() : defaultHeaders();
}

function authFetchMock(input, init) {
  const base = init || {};
  const own = base.headers || {};
  const merged = { ...currentHeaders(), ...own };
  // Без заголовков и без init — вызываем fetch как есть (как в тестах до миграции на authFetch).
  if (!init && Object.keys(merged).length === 0) return global.fetch(input);
  if (!base.headers && Object.keys(merged).length === 0) return global.fetch(input, base);
  return global.fetch(input, { ...base, headers: merged });
}

module.exports = { authFetchMock };
