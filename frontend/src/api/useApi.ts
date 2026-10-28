import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, getJson, type Params } from "./client";

export interface ApiState<T> {
  data: T | null;
  error: ApiError | null;
  loading: boolean;
  reload: () => void;
}

const CACHE_TTL_MS = 60_000;
const cache = new Map<string, { at: number; data: unknown }>();

export function clearApiCache(): void {
  cache.clear();
}

/** Fetch a GET endpoint with loading/error state and a short in-memory cache. */
export function useApi<T>(path: string | null, params: Params = {}): ApiState<T> {
  const key = path ? `${path}?${JSON.stringify(params)}` : null;
  const cached = key ? cache.get(key) : undefined;
  const fresh = cached && Date.now() - cached.at < CACHE_TTL_MS ? (cached.data as T) : null;
  const [data, setData] = useState<T | null>(fresh);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState<boolean>(Boolean(key) && fresh === null);
  const [nonce, setNonce] = useState(0);
  const paramsRef = useRef(params);
  paramsRef.current = params;

  useEffect(() => {
    if (!key || !path) return;
    const hit = cache.get(key);
    if (nonce === 0 && hit && Date.now() - hit.at < CACHE_TTL_MS) {
      setData(hit.data as T);
      setLoading(false);
      setError(null);
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    getJson<T>(path, paramsRef.current, controller.signal)
      .then((result) => {
        cache.set(key, { at: Date.now(), data: result });
        setData(result);
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setError(err instanceof ApiError ? err : new ApiError("Unexpected error", null));
        setData(null);
        setLoading(false);
      });
    return () => controller.abort();
  }, [key, path, nonce]);

  const reload = useCallback(() => {
    if (key) cache.delete(key);
    setNonce((n) => n + 1);
  }, [key]);

  return { data, error, loading, reload };
}
