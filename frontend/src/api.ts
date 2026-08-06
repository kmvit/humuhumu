// Тонкая обёртка над fetch с хранением JWT в localStorage
// и прозрачным обновлением access-токена по refresh при 401.
const ACCESS_KEY = "access_token";
const REFRESH_KEY = "refresh_token";

export function setTokens(access: string, refresh?: string) {
  localStorage.setItem(ACCESS_KEY, access);
  if (refresh) localStorage.setItem(REFRESH_KEY, refresh);
}

export function clearTokens() {
  localStorage.removeItem(ACCESS_KEY);
  localStorage.removeItem(REFRESH_KEY);
}

export function getToken(): string | null {
  return localStorage.getItem(ACCESS_KEY);
}

function getRefresh(): string | null {
  return localStorage.getItem(REFRESH_KEY);
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

// Обновление access по refresh. Single-flight: параллельные 401 ждут один запрос.
let refreshing: Promise<string | null> | null = null;

function refreshAccess(): Promise<string | null> {
  if (refreshing) return refreshing;
  const refresh = getRefresh();
  if (!refresh) return Promise.resolve(null);
  refreshing = fetch("/api/auth/token/refresh/", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh }),
  })
    .then(async (res) => {
      if (!res.ok) {
        clearTokens(); // refresh протух/невалиден — нужен повторный вход
        return null;
      }
      const body = await res.json();
      setTokens(body.access); // refresh оставляем прежний
      return body.access as string;
    })
    .catch(() => null)
    .finally(() => {
      refreshing = null;
    });
  return refreshing;
}

async function request<T>(path: string, options: RequestInit, retry: boolean): Promise<T> {
  const token = getToken();
  // Для FormData не выставляем Content-Type — браузер сам добавит boundary.
  const isForm = options.body instanceof FormData;
  const res = await fetch(`/api${path}`, {
    ...options,
    headers: {
      ...(isForm ? {} : { "Content-Type": "application/json" }),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...options.headers,
    },
  });

  // access протух — пробуем один раз обновить его по refresh и повторить запрос
  if (res.status === 401 && retry && !path.startsWith("/auth/") && getRefresh()) {
    const newAccess = await refreshAccess();
    if (newAccess) return request<T>(path, options, false);
  }

  if (!res.ok) {
    let detail = `Ошибка ${res.status}`;
    try {
      const body = await res.json();
      detail = body.detail || JSON.stringify(body);
    } catch {
      /* тело не JSON */
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  return request<T>(path, options, true);
}

// Удобные методы
export const get = <T>(path: string) => api<T>(path);
export const post = <T>(path: string, body: unknown) =>
  api<T>(path, { method: "POST", body: JSON.stringify(body) });
export const patch = <T>(path: string, body: unknown) =>
  api<T>(path, { method: "PATCH", body: JSON.stringify(body) });
// Загрузка файла (multipart): body — FormData, Content-Type ставит браузер.
export const postForm = <T>(path: string, form: FormData) =>
  api<T>(path, { method: "POST", body: form });
