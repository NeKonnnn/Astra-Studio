/**
 * Определение ошибки логина «у пользователя нет нужной роли».
 *
 * Приоритет:
 * 1. машинный код ошибки от backend (`code` / `error_code` / строка-код целиком);
 * 2. фразы целиком (после токенизации по не-буквам/цифрам) — «нет необходимой роли»,
 *    «missing role», «insufficient permissions», «доступ запрещён»;
 * 3. одиночное слово `forbidden` — только если backend ответил HTTP 403.
 *
 * Отдельные слова `role`/`роль`/`forbidden` в произвольном тексте (например, в сообщении
 * о неверном пароле или в логине `role_admin`) ложных срабатываний не дают.
 */

export const MISSING_ROLE_LOGIN_MESSAGE =
  'У вас нет необходимой роли для взаимодействия с AstraChat. Обратитесь к администратору проекта';

const NON_WORD_RE = new RegExp('[^\\p{L}\\p{N}]+', 'u');

/** Токенизирует строку в «слово слово слово» (нижний регистр, ё → е). */
function toWordBoundedText(value: string): string {
  const words = value
    .toLowerCase()
    .replace(/ё/g, 'е')
    .split(NON_WORD_RE)
    .filter(Boolean);
  return words.join(' ');
}

/** Коды ошибок backend (нормализованные: нижний регистр, разделители → `_`). */
const ROLE_ERROR_CODES = new Set([
  'missing_role',
  'missing_roles',
  'role_missing',
  'no_role',
  'no_roles',
  'role_required',
  'roles_required',
  'required_role',
  'required_roles',
  'insufficient_role',
  'insufficient_roles',
  'insufficient_permissions',
  'insufficient_privileges',
  'forbidden_role',
]);

function normalizeCode(value: string): string {
  return value
    .trim()
    .toLowerCase()
    .split(NON_WORD_RE)
    .filter(Boolean)
    .join('_');
}

const RU_ROLE = '(?:роль|роли|ролей)';
const EN_ROLE = '(?:roles?)';

/** Фразы поверх токенизированного текста. До двух «связующих» слов между отрицанием и ролью. */
const ROLE_ERROR_PHRASES: RegExp[] = [
  new RegExp(`(?:^| )(?:нет|отсутствует|отсутствуют|без|недостаточно)(?: [^ ]+){0,2} ${RU_ROLE}(?: |$)`, 'u'),
  new RegExp(`(?:^| )(?:no|missing|lacks?|without|insufficient|required)(?: [^ ]+){0,2} ${EN_ROLE}(?: |$)`, 'u'),
  /(?:^| )insufficient (?:permissions?|privileges?|rights)(?: |$)/u,
  /(?:^| )доступ (?:запрещен|запрещено)(?: |$)/u,
  /(?:^| )access denied(?: |$)/u,
];

function isRoleErrorCode(value: unknown): boolean {
  return typeof value === 'string' && ROLE_ERROR_CODES.has(normalizeCode(value));
}

/** Текст содержит осмысленную фразу про отсутствие роли (без учёта одиночных `role`/`forbidden`). */
export function containsRoleErrorMarker(value: unknown): boolean {
  if (typeof value !== 'string') return false;
  const text = toWordBoundedText(value);
  if (!text) return false;
  return ROLE_ERROR_PHRASES.some((re) => re.test(text));
}

function containsForbiddenWord(value: unknown): boolean {
  if (typeof value !== 'string') return false;
  return toWordBoundedText(value).split(' ').includes('forbidden');
}

/**
 * @param detail — поле `detail` ответа backend (строка или объект).
 * @param parsedMessage — человекочитаемое сообщение, уже извлечённое из detail.
 * @param httpStatus — HTTP-статус ответа; при 403 слово `forbidden` считается признаком роли.
 */
export function isMissingRoleLoginError(
  detail: unknown,
  parsedMessage?: string,
  httpStatus?: number,
): boolean {
  // 1) код ошибки
  if (isRoleErrorCode(detail)) return true;
  if (detail && typeof detail === 'object') {
    const obj = detail as Record<string, unknown>;
    if (isRoleErrorCode(obj.code) || isRoleErrorCode(obj.error_code) || isRoleErrorCode(obj.error)) {
      return true;
    }
  }

  // 2) фразы
  if (containsRoleErrorMarker(parsedMessage) || containsRoleErrorMarker(detail)) return true;
  if (detail && typeof detail === 'object') {
    const obj = detail as Record<string, unknown>;
    if (
      containsRoleErrorMarker(obj.message) ||
      containsRoleErrorMarker(obj.detail) ||
      containsRoleErrorMarker(obj.error)
    ) {
      return true;
    }
  }

  // 3) «forbidden» только вместе с HTTP 403
  if (httpStatus === 403) {
    if (containsForbiddenWord(parsedMessage) || containsForbiddenWord(detail)) return true;
    if (detail && typeof detail === 'object') {
      const obj = detail as Record<string, unknown>;
      if (containsForbiddenWord(obj.message) || containsForbiddenWord(obj.detail)) return true;
    }
  }
  return false;
}
