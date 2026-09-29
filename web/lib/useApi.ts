"use client";

import { useCallback, useEffect, useState } from "react";

import { api, ApiError } from "@/lib/api";

interface ApiState<T> {
  data?: T;
  error?: ApiError;
  loading: boolean;
}

/** GET `path` (null = don't fetch yet), optionally re-fetching every `refreshMs`. */
export function useApi<T>(path: string | null, refreshMs?: number) {
  const [state, setState] = useState<ApiState<T>>({ loading: path !== null });
  const [version, setVersion] = useState(0);

  useEffect(() => {
    if (path === null) return;
    let cancelled = false;
    api<T>(path).then(
      (data) => {
        if (!cancelled) setState({ data, loading: false });
      },
      (error: unknown) => {
        if (cancelled) return;
        const apiError = error instanceof ApiError ? error : new ApiError(0, "Network error.");
        setState((previous) => ({ ...previous, error: apiError, loading: false }));
      },
    );
    return () => {
      cancelled = true;
    };
  }, [path, version]);

  useEffect(() => {
    if (!refreshMs) return;
    const timer = setInterval(() => setVersion((v) => v + 1), refreshMs);
    return () => clearInterval(timer);
  }, [refreshMs]);

  const reload = useCallback(() => setVersion((v) => v + 1), []);
  return { ...state, reload };
}
