export const INLINE_ATTACH_ALLOWED_TYPES_LABEL =
  'PDF (.pdf), Word (.docx, .docm, .doc), Excel (.xlsx, .xls, .xlsm), PowerPoint (.pptx, .ppt), текст (.txt, .md, .log, .rtf), изображения (.jpg, .jpeg, .png, .webp, .gif, .bmp)';

/** Значение accept для <input type="file"> кнопки «+» — тот же список, что в подписи. */
export const INLINE_ATTACH_ACCEPT =
  '.pdf,.docx,.docm,.doc,.xlsx,.xls,.xlsm,.pptx,.ppt,.txt,.md,.log,.rtf,.jpg,.jpeg,.png,.webp,.gif,.bmp';

/** Лимит для inline-вложений через «+» (фронт и POST /api/documents/attach). */
export const INLINE_ATTACHMENT_MAX_BYTES = 20 * 1024 * 1024;
export const INLINE_ATTACHMENT_MAX_SIZE_MB = 20;

export const INLINE_ATTACH_WAIT_BEFORE_SEND_MESSAGE =
  'Пожалуйста, подождите, пока загруженный или обрабатываемый в данный момент файл не завершит обработку, прежде чем отправлять сообщение.';

export const INLINE_ATTACH_OVERSIZED_MESSAGE =
  'Файл не должен превышать 20 МБ! Рекомендуем поместить его в агентный или проектный РАГ';

const IMAGE_MIME_TYPES = [
  'image/jpeg',
  'image/jpg',
  'image/png',
  'image/webp',
  'image/gif',
  'image/bmp',
];
const DOC_MIME_TYPES = [
  'application/pdf',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  'application/vnd.ms-word.document.macroEnabled.12',
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  'application/vnd.ms-excel.sheet.macroEnabled.12',
  'application/vnd.ms-excel',
  'application/msword',
  'application/vnd.ms-powerpoint',
  'application/vnd.openxmlformats-officedocument.presentationml.presentation',
  'text/plain',
  'text/markdown',
  'application/rtf',
  'text/rtf',
];

export function getInlineAttachmentExtension(filename: string): string {
  const name = filename || '';
  const dot = name.lastIndexOf('.');
  if (dot <= 0 || dot === name.length - 1) return '';
  return name.slice(dot).toLowerCase();
}

export function isInlineAttachFileAllowed(file: File): boolean {
  const isImage =
    IMAGE_MIME_TYPES.includes(file.type) || /\.(jpe?g|png|webp|gif|bmp)$/i.test(file.name);
  const isDoc =
    DOC_MIME_TYPES.includes(file.type) ||
    /\.(pdf|docx|docm|doc|xlsx|xls|xlsm|pptx|ppt|txt|md|log|rtf)$/i.test(file.name);
  return isImage || isDoc;
}

export function isInlineAttachFileTooLarge(file: File): boolean {
  return file.size > INLINE_ATTACHMENT_MAX_BYTES;
}

export function isInlineAttachSizeErrorMessage(detail: string): boolean {
  return /размер|size|413|превышает|too large|limit/i.test(detail);
}

export function buildUnsupportedInlineAttachMessage(filename: string): string {
  const ext = getInlineAttachmentExtension(filename);
  const extLabel = ext || 'без расширения';
  return `Данный тип файлов ${extLabel} не поддерживается. Допускаются следующие файлы: ${INLINE_ATTACH_ALLOWED_TYPES_LABEL}.`;
}

export function buildOversizedInlineAttachMessage(_filename?: string, _fileSize?: number): string {
  return INLINE_ATTACH_OVERSIZED_MESSAGE;
}
