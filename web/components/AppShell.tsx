"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { type ReactNode, useEffect } from "react";

import { UserContext } from "@/components/UserContext";
import { api } from "@/lib/api";
import type { User } from "@/lib/types";
import { useApi } from "@/lib/useApi";

const NAV = [
  { href: "/dashboard", label: "Sites", adminOnly: false },
  { href: "/alerts", label: "Alerts", adminOnly: false },
  { href: "/ops", label: "Ops", adminOnly: true },
];

/** Everything behind login: checks the session once, then renders the nav and the page. */
export function AppShell({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const { data: user, error } = useApi<User>("/auth/me");

  useEffect(() => {
    if (error?.status === 401) router.replace(`/login?next=${encodeURIComponent(pathname)}`);
  }, [error, pathname, router]);

  async function logOut() {
    await api("/auth/logout", { method: "POST" });
    router.replace("/login");
  }

  if (!user) {
    return (
      <p className="p-8 text-sm text-zinc-500" role="status">
        {error && error.status !== 401 ? error.message : "Loading…"}
      </p>
    );
  }

  return (
    <UserContext.Provider value={user}>
      <header className="border-b border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-950">
        <nav className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3" aria-label="Main">
          <Link href="/dashboard" className="font-semibold tracking-tight">
            tag-monitor
          </Link>
          {NAV.filter((item) => user.is_admin || !item.adminOnly).map((item) => (
            <Link
              key={item.href}
              href={item.href}
              aria-current={pathname.startsWith(item.href) ? "page" : undefined}
              className="text-sm text-zinc-600 hover:text-zinc-900 aria-[current=page]:font-medium aria-[current=page]:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-100 dark:aria-[current=page]:text-zinc-100"
            >
              {item.label}
            </Link>
          ))}
          <span className="ml-auto text-sm text-zinc-500">{user.email}</span>
          <button onClick={logOut} className="text-sm text-zinc-600 underline-offset-4 hover:underline dark:text-zinc-400">
            Log out
          </button>
        </nav>
      </header>
      <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-8">{children}</main>
    </UserContext.Provider>
  );
}
