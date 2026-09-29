import { Suspense } from "react";

import { AuthForm } from "@/components/AuthForm";

export const metadata = { title: "Log in · tag-monitor" };

export default function LoginPage() {
  // useSearchParams (for ?next=) needs a Suspense boundary when the page is prerendered.
  return (
    <Suspense>
      <AuthForm mode="login" />
    </Suspense>
  );
}
