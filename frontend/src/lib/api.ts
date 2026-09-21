// lib/api.ts
// The single gateway to the FastAPI backend.
//
// CRITICAL: every request sets `credentials: "include"` so the httpOnly
// session cookie travels with it. Without this, auth silently fails — the
// cookie never reaches the backend. This wrapper exists so that flag can
// never be forgotten at a call site.

const BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8001";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...options,
    credentials: "include", // <-- the cookie carrier; do not remove
    headers: {
      "Content-Type": "application/json",
      ...(options.headers ?? {}),
    },
  });

  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail);
  }

  // 204 / empty bodies
  const text = await res.text();
  return (text ? JSON.parse(text) : null) as T;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, {
      method: "POST",
      body: body === undefined ? undefined : JSON.stringify(body),
    }),

  /**
   * Multipart upload (used for screen recordings).
   *
   * Deliberately does NOT go through `request`: that helper always sets
   * Content-Type: application/json, which would corrupt a multipart body.
   * The browser must set Content-Type itself so it can include the boundary
   * marker, so we omit the header entirely here.
   */
  upload: async <T>(path: string, file: File): Promise<T> => {
    const form = new FormData();
    form.append("file", file);

    const res = await fetch(`${BASE}${path}`, {
      method: "POST",
      credentials: "include",
      body: form,
    });

    if (!res.ok) {
      let detail = res.statusText;
      try {
        const body = await res.json();
        detail = typeof body.detail === "string" ? body.detail : detail;
      } catch {
        /* non-JSON error body */
      }
      throw new ApiError(res.status, detail);
    }
    const text = await res.text();
    return (text ? JSON.parse(text) : null) as T;
  },
};