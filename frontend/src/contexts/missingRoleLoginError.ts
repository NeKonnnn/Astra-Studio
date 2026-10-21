/** Определение ошибки логина «у пользователя нет нужной роли» (без ложных срабатываний на «пароль»). */

export const MISSING_ROLE_LOGIN_MESSAGE =
  'У вас нет необходимой роли для взаимодействия с AstraChat. Обратитесь к администратору проекта';

/**
 * Маркеры — целые слова/фразы (после токенизации по не-буквам/цифрам).
 * Подстрока «роль» внутри «пароль» не должна совпадать.
 */
const ROLE_ERROR_MARKERS = [
  'роль',
  'roles',
  'role',
  'insufficient',
  'forbidden',
  'доступ запрещен',
  'доступ запрещён',
];

const NON_WORD_RE = new RegExp('[^\\p{L}\\p{N}]+', 'u');

/** Нормализует строку в «слово слово слово» с пробелами по краям для поиска по границам слов. */
function toWordBoundedText(value: string): string {
  const words = value.toLowerCase().split(NON_WORD_RE).filter(Boolean);
  return ` ${words.join(' ')} `;
}

const BOUNDED_MARKERS = ROLE_ERROR_MARKERS.map((marker) => ` ${toWordBoundedText(marker).trim()} `);

export function containsRoleErrorMarker(value: unknown): boolean {
  if (typeof value !== 'string') return false;
  const text = toWordBoundedText(value);
  return BOUNDED_MARKERS.some((marker) => text.includes(marker));
}

export function isMissingRoleLoginError(detail: unknown, parsedMessage?: string): boolean {
  if (containsRoleErrorMarker(parsedMessage)) return true;
  if (containsRoleErrorMarker(detail)) return true;
  if (detail && typeof detail === 'object') {
    const obj = detail as Record<string, unknown>;
    return (
      containsRoleErrorMarker(obj.message) ||
      containsRoleErrorMarker(obj.detail) ||
      containsRoleErrorMarker(obj.error) ||
      containsRoleErrorMarker(obj.code)
    );
  }
  return false;
}
