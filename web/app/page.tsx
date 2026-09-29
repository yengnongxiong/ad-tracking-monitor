import Link from "next/link";

import { FindingsHighlights } from "@/components/FindingsHighlights";
import { button } from "@/components/ui";

const CHECKS = [
  {
    title: "Your tags actually fire",
    body: "We load your page in a real browser and watch the network: is your Meta Pixel sending PageView? Is GA4 collecting? Is your Google Ads tag there? Installed isn't the same as working.",
  },
  {
    title: "It's fast on phones",
    body: "Most ad clicks come from phones. We time how long your main content takes to appear, against Google's thresholds.",
  },
  {
    title: "It looks right on mobile",
    body: "No missing viewport tag, no page that scrolls sideways, plus a screenshot of what visitors see.",
  },
  {
    title: "The page works",
    body: "It loads, over HTTPS, without errors, redirect chains, or a surprise trip to another website.",
  },
];

export default function Home() {
  return (
    <main className="flex-1">
      <header className="mx-auto flex max-w-5xl items-center justify-between px-6 py-5">
        <span className="font-semibold tracking-tight">tag-monitor</span>
        <nav className="flex items-center gap-4 text-sm">
          <Link href="/login" className="hover:underline">Log in</Link>
          <Link href="/signup" className={button.primary}>Start monitoring</Link>
        </nav>
      </header>

      <section className="mx-auto max-w-5xl px-6 pb-16 pt-12">
        <h1 className="max-w-3xl text-4xl font-semibold tracking-tight sm:text-5xl">
          Know the moment your Meta Pixel or Google tags stop firing.
        </h1>
        <p className="mt-6 max-w-2xl text-lg text-zinc-600 dark:text-zinc-400">
          Tags break silently: a theme update, a removed Tag Manager container, a new cookie
          banner. Your ads keep spending while reporting and optimization fall apart.
          tag-monitor checks your landing pages on a schedule and emails you once, in plain
          English, when something that used to work breaks. Then again when it&apos;s fixed.
        </p>
        <div className="mt-8 flex gap-3">
          <Link href="/signup" className={button.primary}>Monitor your first page</Link>
        </div>
      </section>

      <section className="border-t border-zinc-200 bg-zinc-50 py-16 dark:border-zinc-800 dark:bg-zinc-900">
        <div className="mx-auto max-w-5xl px-6">
          <h2 className="text-2xl font-semibold tracking-tight">What we check, every time</h2>
          <div className="mt-8 grid gap-6 sm:grid-cols-2">
            {CHECKS.map((check) => (
              <div key={check.title} className="rounded-lg bg-white p-6 shadow-sm dark:bg-zinc-950">
                <h3 className="font-medium">{check.title}</h3>
                <p className="mt-2 text-sm text-zinc-600 dark:text-zinc-400">{check.body}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="mx-auto max-w-5xl px-6 py-16">
        <h2 className="text-2xl font-semibold tracking-tight">One clear alert, not fifty</h2>
        <p className="mt-4 max-w-2xl text-zinc-600 dark:text-zinc-400">
          When a check fails we look again ten minutes later before emailing you, so a passing
          hiccup never wakes you up. If your whole page is down you get one email about that,
          not one per tag. Every email says what we saw, why it matters and what to do.
        </p>
        <h2 className="mt-12 text-2xl font-semibold tracking-tight">What we&apos;re finding</h2>
        <FindingsHighlights />
      </section>
    </main>
  );
}
