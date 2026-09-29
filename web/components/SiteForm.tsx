"use client";

import { type FormEvent, type ReactNode, useState } from "react";

import { button, field } from "@/components/ui";
import { ApiError } from "@/lib/api";
import { INTERVALS } from "@/lib/format";
import type { Site, SiteInput } from "@/lib/types";

function ids(value: FormDataEntryValue | null): string[] {
  return String(value ?? "")
    .split(/[\s,]+/)
    .map((id) => id.trim())
    .filter(Boolean);
}

function optional(value: FormDataEntryValue | null): string | null {
  const text = String(value ?? "").trim();
  return text === "" ? null : text;
}

function Field({ label, hint, children }: { label: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <label className="block text-sm font-medium">
      {label}
      {children}
      {hint && <span className="mt-1 block text-xs font-normal text-zinc-500">{hint}</span>}
    </label>
  );
}

/** Add and edit share this form. `onSubmit` throws ApiError to show the API's message. */
export function SiteForm({
  initial,
  submitLabel,
  onSubmit,
}: {
  initial?: Site;
  submitLabel: string;
  onSubmit: (input: SiteInput) => Promise<void>;
}) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setError(null);
    try {
      await onSubmit({
        url: String(form.get("url")).trim(),
        name: optional(form.get("name")) ?? undefined,
        check_interval_minutes: Number(form.get("interval")) as SiteInput["check_interval_minutes"],
        alert_email: optional(form.get("alert_email")) ?? undefined,
        expected_meta_pixel_ids: ids(form.get("meta")),
        expected_ga4_ids: ids(form.get("ga4")),
        expected_google_ads_ids: ids(form.get("ads")),
        ad_headline: optional(form.get("ad_headline")),
        ad_primary_text: optional(form.get("ad_primary_text")),
        ad_cta: optional(form.get("ad_cta")),
      });
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Couldn't save. Try again.");
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="max-w-2xl space-y-8">
      <section className="space-y-4">
        <Field label="Landing page URL" hint="The exact page your ads send people to.">
          <input
            name="url"
            type="url"
            required
            placeholder="https://yourshop.com/spring-sale"
            defaultValue={initial?.url}
            className={field}
          />
        </Field>
        <Field label="Name" hint="Optional. Defaults to the domain.">
          <input name="name" defaultValue={initial?.name} maxLength={200} className={field} />
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="How often to check">
            <select name="interval" defaultValue={initial?.check_interval_minutes ?? 1440} className={field}>
              {INTERVALS.map((interval) => (
                <option key={interval.value} value={interval.value}>
                  {interval.label}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Send alerts to" hint="Defaults to your account email.">
            <input name="alert_email" type="email" defaultValue={initial?.alert_email} className={field} />
          </Field>
        </div>
      </section>

      <fieldset className="space-y-4">
        <legend className="text-base font-semibold">Tracking IDs to expect (optional)</legend>
        <p className="text-sm text-zinc-600 dark:text-zinc-400">
          With these filled in, we also alert you if a tag disappears or is swapped for someone
          else&apos;s. Separate several IDs with commas.
        </p>
        <Field label="Meta Pixel ID" hint="Meta Events Manager → Data sources → your pixel. A 15–16 digit number.">
          <input name="meta" defaultValue={initial?.expected_meta_pixel_ids.join(", ")} placeholder="1234567890123456" className={field} />
        </Field>
        <Field label="GA4 measurement ID" hint="Google Analytics → Admin → Data streams → your web stream. Starts with G-.">
          <input name="ga4" defaultValue={initial?.expected_ga4_ids.join(", ")} placeholder="G-ABC123XYZ" className={field} />
        </Field>
        <Field label="Google Ads conversion ID" hint="Google Ads → Goals → Conversions → Tag setup. Starts with AW-.">
          <input name="ads" defaultValue={initial?.expected_google_ads_ids.join(", ")} placeholder="AW-123456789" className={field} />
        </Field>
      </fieldset>

      <fieldset className="space-y-4">
        <legend className="text-base font-semibold">Ad copy (optional)</legend>
        <p className="text-sm text-zinc-600 dark:text-zinc-400">
          Paste the ad that sends traffic here and we&apos;ll check the page matches its promise.
        </p>
        <Field label="Headline">
          <input name="ad_headline" defaultValue={initial?.ad_headline ?? ""} maxLength={200} className={field} />
        </Field>
        <Field label="Primary text">
          <textarea name="ad_primary_text" defaultValue={initial?.ad_primary_text ?? ""} maxLength={2000} rows={3} className={field} />
        </Field>
        <Field label="Call to action" hint='For example "Shop now".'>
          <input name="ad_cta" defaultValue={initial?.ad_cta ?? ""} maxLength={100} className={field} />
        </Field>
      </fieldset>

      {error && (
        <p role="alert" className="rounded-md bg-red-50 p-3 text-sm text-red-800 dark:bg-red-950 dark:text-red-200">
          {error}
        </p>
      )}
      <button type="submit" disabled={busy} className={button.primary}>
        {busy ? "Saving…" : submitLabel}
      </button>
    </form>
  );
}
