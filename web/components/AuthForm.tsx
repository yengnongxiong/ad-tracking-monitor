"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { type FormEvent, useState } from "react";

import { button, field } from "@/components/ui";
import { api, ApiError } from "@/lib/api";

export function AuthForm({ mode }: { mode: "login" | "signup" }) {
  const router = useRouter();
  const next = useSearchParams().get("next");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const isSignup = mode === "signup";

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setError(null);
    try {
      await api(`/auth/${mode}`, {
        method: "POST",
        body: { email: form.get("email"), password: form.get("password") },
      });
      // Only follow same-site paths, never a full URL from the query string.
      router.replace(next?.startsWith("/") && !next.startsWith("//") ? next : "/dashboard");
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Network error. Try again.");
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto flex w-full max-w-sm flex-1 flex-col justify-center px-4 py-16">
      <Link href="/" className="mb-8 font-semibold tracking-tight">
        tag-monitor
      </Link>
      <h1 className="text-2xl font-semibold">{isSignup ? "Create your account" : "Log in"}</h1>
      <form onSubmit={submit} className="mt-6 space-y-4" noValidate>
        <label className="block text-sm font-medium">
          Email
          <input name="email" type="email" autoComplete="email" required className={field} />
        </label>
        <label className="block text-sm font-medium">
          Password
          <input
            name="password"
            type="password"
            autoComplete={isSignup ? "new-password" : "current-password"}
            minLength={8}
            required
            className={field}
          />
          {isSignup && <span className="mt-1 block text-xs text-zinc-500">At least 8 characters.</span>}
        </label>
        {error && (
          <p role="alert" className="rounded-md bg-red-50 p-3 text-sm text-red-800 dark:bg-red-950 dark:text-red-200">
            {error}
          </p>
        )}
        <button type="submit" disabled={busy} className={`${button.primary} w-full`}>
          {busy ? "One moment…" : isSignup ? "Create account" : "Log in"}
        </button>
      </form>
      <p className="mt-6 text-sm text-zinc-600 dark:text-zinc-400">
        {isSignup ? "Already have an account? " : "New here? "}
        <Link href={isSignup ? "/login" : "/signup"} className="font-medium underline underline-offset-4">
          {isSignup ? "Log in" : "Create an account"}
        </Link>
      </p>
    </main>
  );
}
