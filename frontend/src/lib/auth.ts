// lib/auth.ts
// Auth state derives entirely from the backend session cookie. Because the
// cookie is httpOnly, JS can't read the token — "are we logged in?" is simply
// "does /auth/me return 200?". TanStack Query caches that answer.

"use client";

import {
  useQuery,
  useMutation,
  useQueryClient,
} from "@tanstack/react-query";
import { api, ApiError } from "./api";
import type { UserPublic } from "./types";

export function useUser() {
  return useQuery<UserPublic | null>({
    queryKey: ["me"],
    queryFn: async () => {
      try {
        return await api.get<UserPublic>("/auth/me");
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) return null;
        throw e;
      }
    },
    staleTime: 30_000,
    retry: false,
  });
}

export function useLogin() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { email: string; password: string }) =>
      api.post<UserPublic>("/auth/login", body),
    onSuccess: (user) => qc.setQueryData(["me"], user),
  });
}

export function useRegister() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      email: string;
      password: string;
      display_name: string;
    }) => api.post<UserPublic>("/auth/register", body),
    onSuccess: (user) => qc.setQueryData(["me"], user),
  });
}

export function useLogout() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post("/auth/logout"),
    onSuccess: () => qc.setQueryData(["me"], null),
  });
}