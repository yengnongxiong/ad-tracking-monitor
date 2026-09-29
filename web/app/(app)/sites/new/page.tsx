"use client";

import { useRouter } from "next/navigation";

import { SiteForm } from "@/components/SiteForm";
import { api } from "@/lib/api";
import type { Site, SiteInput } from "@/lib/types";

export default function NewSitePage() {
  const router = useRouter();

  async function create(input: SiteInput) {
    const site = await api<Site>("/sites", { method: "POST", body: input });
    router.push(`/sites/${site.id}`);
  }

  return (
    <>
      <h1 className="mb-6 text-2xl font-semibold tracking-tight">Add a landing page</h1>
      <SiteForm submitLabel="Add page and run the first check" onSubmit={create} />
    </>
  );
}
