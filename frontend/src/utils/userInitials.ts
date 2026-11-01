/**
 * Инициалы для аватара без фото: первая буква имени + первая буква фамилии.
 * «Петр Кашин» → «ПК»; одно слово или логин → первые 2 символа.
 */
export function getUserInitials(
  fullName?: string | null,
  username?: string | null,
): string {
  const name = (fullName || '').trim();
  if (name) {
    const parts = name.split(/\s+/).filter(Boolean);
    if (parts.length >= 2) {
      const first = parts[0].charAt(0);
      const last = parts[parts.length - 1].charAt(0);
      if (first && last) {
        return `${first}${last}`.toUpperCase();
      }
    }
    if (parts.length === 1) {
      const word = parts[0];
      if (word.length >= 2) {
        return word.slice(0, 2).toUpperCase();
      }
      if (word.length === 1) {
        return word.toUpperCase();
      }
    }
  }

  const login = (username || '').trim();
  if (login.length >= 2) {
    return login.slice(0, 2).toUpperCase();
  }
  if (login.length === 1) {
    return login.toUpperCase();
  }

  return '?';
}
