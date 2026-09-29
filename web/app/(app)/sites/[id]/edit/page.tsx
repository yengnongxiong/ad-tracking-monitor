"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";

import { SiteForm } from "@/components/SiteForm";
import { button } from "@/components/ui";
import { api } from "@/lib/api";
import type { SiteDetail, SiteInput } from "@/lib/types";
import { useApi } from "@/lib/useApi";

export default function EditSitePage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const { data: site, error } = useApi<SiteDetail>(`/sites/${id}`);

  async function save(input: SiteInput) {
    await api(`/sites/${id}`, { method: "PATCH", body: input });
    router.push(`/sites/${id}`);
  }

  async function togglePause() {
    if (!site) return;
    await api(`/sites/${id}`, { method: "PATCH", body: { paused: !site.paused } });
    router.push(`/sites/${id}`);
  }

  async function remove() {
    if (!window.confirm("Stop monitoring this page and delete its history?")) return;
    await api(`/sites/${id}`, { method: "DELETE" });
    router.push("/dashboard");
  }

  if (error) return <p role="alert" className="text-sm text-red-700">{error.message}</p>;
  if (!site) return <p className="text-sm text-zinc-500">Loading…</p>;

  return (
    <>
      <Link href={`/sites/${id}`} className="text-sm text-zinc-600 hover:underline dark:text-zinc-400">
        ← {site.name}
      </Link>
      <h1 className="mb-6 mt-2 text-2xl font-semibold tracking-tight">Edit page</h1>
      <SiteForm initial={site} submitLabel="Save changes" onSubmit={save} />
      <div className="mt-12 flex flex-wrap gap-3 border-t border-zinc-200 pt-6 dark:border-zinc-800">
        <button onClick={togglePause} className={button.secondary}>
          {site.paused ? "Resume checks" : "Pause checks"}
        </button>
        <button onClick={remove} className={button.danger}>
          Delete page
        </button>
      </div>
    </>
  );
}
