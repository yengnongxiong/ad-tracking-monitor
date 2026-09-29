import { AppShell } from "@/components/AppShell";

export default function SignedInLayout({ children }: LayoutProps<"/">) {
  return <AppShell>{children}</AppShell>;
}
