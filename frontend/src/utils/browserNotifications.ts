/**
 * Утилита для работы с браузерными уведомлениями
 */

export interface NotificationPermission {
  granted: boolean;
  denied: boolean;
  default: boolean;
}

/**
 * Проверяет, поддерживаются ли браузерные уведомления
 */
export function isNotificationSupported(): boolean {
  return 'Notification' in window;
}

/**
 * Получает текущий статус разрешения на уведомления
 */
export function getNotificationPermission(): NotificationPermission {
  if (!isNotificationSupported()) {
    return { granted: false, denied: false, default: false };
  }

  const permission = Notification.permission;
  return {
    granted: permission === 'granted',
    denied: permission === 'denied',
    default: permission === 'default',
  };
}

/**
 * Запрашивает разрешение на показ уведомлений
 * @returns Promise<boolean> - true если разрешение получено
 */
export async function requestNotificationPermission(): Promise<boolean> {
  if (!isNotificationSupported()) {
    console.warn('Браузерные уведомления не поддерживаются');
    return false;
  }

  if (Notification.permission === 'granted') {
    return true;
  }

  if (Notification.permission === 'denied') {
    console.warn('Разрешение на уведомления было отклонено');
    return false;
  }

  try {
    const permission = await Notification.requestPermission();
    return permission === 'granted';
  } catch (error) {
    console.error('Ошибка при запросе разрешения на уведомления:', error);
    return false;
  }
}

/**
 * Показывает браузерное уведомление
 * @param title - Заголовок уведомления
 * @param options - Дополнительные опции уведомления
 */
export function showBrowserNotification(
  title: string,
  options?: NotificationOptions
): void {
  console.log('[Browser Notifications] showBrowserNotification вызвана:', { title, options });
  
  if (!isNotificationSupported()) {
    console.warn('[Browser Notifications] Браузерные уведомления не поддерживаются');
    return;
  }

  if (Notification.permission !== 'granted') {
    console.warn('[Browser Notifications] Разрешение на уведомления не получено. Текущий статус:', Notification.permission);
    return;
  }

  try {
    console.log('[Browser Notifications] Создаем уведомление...');
    const notification = new Notification(title, {
      icon: '/astra.ico', // Иконка приложения
      badge: '/astra.ico',
      tag: 'astrachat-message', // Тег для группировки уведомлений
      requireInteraction: false, // Уведомление автоматически закроется
      silent: false, // Воспроизводить звук
      ...options,
    });

    console.log('[Browser Notifications] Уведомление создано успешно');

    // Автоматически закрываем уведомление через 5 секунд
    setTimeout(() => {
      notification.close();
      console.log('[Browser Notifications] Уведомление закрыто автоматически');
    }, 5000);

    // Обработчик клика на уведомление - фокусируем окно
    notification.onclick = () => {
      console.log('[Browser Notifications] Клик по уведомлению');
      window.focus();
      notification.close();
    };

    // Обработчики событий для отладки
    notification.onshow = () => {
      console.log('[Browser Notifications] Уведомление показано');
    };

    notification.onerror = (error) => {
      console.error('[Browser Notifications] Ошибка уведомления:', error);
    };

    notification.onclose = () => {
      console.log('[Browser Notifications] Уведомление закрыто');
    };
  } catch (error) {
    console.error('[Browser Notifications] Ошибка при показе уведомления:', error);
  }
}

/**
 * Проверяет, включены ли уведомления в настройках
 */
export function areNotificationsEnabled(): boolean {
  const enabled = localStorage.getItem('browser_notifications_enabled');
  return enabled === 'true';
}

/**
 * Включает или выключает уведомления
 */
export function setNotificationsEnabled(enabled: boolean): void {
  localStorage.setItem('browser_notifications_enabled', enabled.toString());
}

