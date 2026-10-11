import type { Metadata } from "next";

// A shared plan is for the people it was sent to: never indexed (ADR 0006). The API says the same in a header.
export const metadata: Metadata = { title: "Shared trip", robots: { index: false, follow: false } };

export default function SharedLayout({ children }: { children: React.ReactNode }) {
  return children;
}
