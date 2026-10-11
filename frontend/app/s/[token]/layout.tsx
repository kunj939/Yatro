import type { Metadata } from "next";
import { fetchShared, summaryLine } from "@/lib/shareCard";

// A shared plan is for the people it was sent to: never indexed (ADR 0006). The API says the same in a header.
// The title, description and card image (opengraph-image.tsx) are what WhatsApp shows when the link is sent.
export async function generateMetadata({ params }: { params: Promise<{ token: string }> }): Promise<Metadata> {
  const { token } = await params;
  const v = await fetchShared(token);
  const title = v ? v.itinerary.title || v.trip.title : "A trip planned with Yatro";
  const description = v ? summaryLine(v) : "Open the full day-by-day plan: travel, stays, sights and meals that fit the budget.";
  return {
    title, description,
    robots: { index: false, follow: false },
    openGraph: { title, description, type: "website", siteName: "Yatro", url: `/s/${token}` },
    twitter: { card: "summary_large_image", title, description },
  };
}

export default function SharedLayout({ children }: { children: React.ReactNode }) {
  return children;
}
