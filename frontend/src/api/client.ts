import { apiConfig } from "../config";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number | null,
    readonly code: string | null = null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export type Params = Record<string, string | number | boolean | null | undefined>;

export function buildUrl(baseUrl: string, path: string, params: Params = {}): string {
  const url = new URL(baseUrl.replace(/\/+$/, "") + path);
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== "") {
      url.searchParams.set(key, String(value));
    }
  }
  return url.toString();
}

const TIMEOUT_MS = 20_000;

/** GET JSON from the public API. Read-only by construction: no other method is exposed. */
export async function getJson<T>(path: string, params: Params = {}, signal?: AbortSignal): Promise<T> {
  if (!apiConfig.baseUrl) {
    throw new ApiError(apiConfig.error ?? "API is not configured", null, "not_configured");
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  const onAbort = () => controller.abort();
  signal?.addEventListener("abort", onAbort);
  try {
    const response = await fetch(buildUrl(apiConfig.baseUrl, path, params), {
      method: "GET",
      headers: { Accept: "application/json" },
      signal: controller.signal,
      credentials: "omit",
    });
    if (!response.ok) {
      let code: string | null = null;
      try {
        code = ((await response.json()) as { error?: string }).error ?? null;
      } catch {
        code = null;
      }
      const message =
        response.status === 429
          ? "Too many requests right now. Please wait a minute and retry."
          : response.status >= 500
            ? "The analytics service is temporarily unavailable."
            : `The request could not be completed (${response.status}).`;
      throw new ApiError(message, response.status, code);
    }
    return (await response.json()) as T;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (signal?.aborted) throw error;
    if (controller.signal.aborted) {
      throw new ApiError("The analytics service took too long to respond.", null, "timeout");
    }
    throw new ApiError("Could not reach the analytics service.", null, "network");
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", onAbort);
  }
}
