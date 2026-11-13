const API_TOKEN_STORAGE_KEY = 'okx-bot-api-token';

function browserStorage(): Storage | null {
  const storage = globalThis.localStorage;
  if (
    storage === undefined
    || storage === null
    || typeof storage.getItem !== 'function'
    || typeof storage.setItem !== 'function'
  ) {
    return null;
  }
  return storage;
}

export function getApiToken(): string {
  return browserStorage()?.getItem(API_TOKEN_STORAGE_KEY)?.trim() ?? '';
}

export function setApiToken(token: string): void {
  const storage = browserStorage();
  if (storage === null) return;
  const trimmed = token.trim();
  if (trimmed) {
    storage.setItem(API_TOKEN_STORAGE_KEY, trimmed);
    return;
  }
  storage.removeItem(API_TOKEN_STORAGE_KEY);
}

export function withApiToken(path: string): string {
  const token = getApiToken();
  if (!token) return path;
  const separator = path.includes('?') ? '&' : '?';
  return `${path}${separator}token=${encodeURIComponent(token)}`;
}
