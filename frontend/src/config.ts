/**
 * The only runtime configuration: the public API base URL (VITE_API_BASE_URL).
 * It is public by design; no secret may ever be placed in a VITE_* variable.
 */
export interface ApiConfig {
  baseUrl: string | null;
  error: string | null;
}

const LOCAL_HOST = /^(localhost|127\.0\.0\.1|\[::1\])$/i;

export function resolveApiConfig(
  raw: string | undefined,
  production: boolean,
  devDefault: string | null = null,
): ApiConfig {
  const value = (raw ?? "").trim().replace(/\/+$/, "");
  if (!value) {
    return production || !devDefault
      ? { baseUrl: null, error: "The dashboard was built without an API address (VITE_API_BASE_URL)." }
      : { baseUrl: devDefault, error: null };
  }
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    return { baseUrl: null, error: "The configured API address is not a valid URL." };
  }
  if (production) {
    if (url.protocol !== "https:") {
      return { baseUrl: null, error: "The API address must use HTTPS in production." };
    }
    if (LOCAL_HOST.test(url.hostname)) {
      return { baseUrl: null, error: "The production build must not point at localhost." };
    }
  }
  if (url.username || url.password || url.search || url.hash) {
    return { baseUrl: null, error: "The API address must not contain credentials or a query." };
  }
  return { baseUrl: value, error: null };
}

// The development fallback is compiled out of production builds (import.meta.env.DEV is false).
export const apiConfig = resolveApiConfig(
  import.meta.env.VITE_API_BASE_URL,
  import.meta.env.PROD,
  import.meta.env.DEV ? "http://localhost:8000" : null,
);
