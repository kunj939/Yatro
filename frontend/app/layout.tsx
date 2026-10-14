import type { Metadata, Viewport } from "next";
import "./globals.css";
import { AuthProvider } from "@/lib/auth";
import { brand } from "@/lib/brand";
import { SpotlightTracker } from "@/components/SpotlightTracker";

// Link previews need absolute image URLs. NEXT_PUBLIC_SITE_URL wins; on Vercel the production domain is known at build.
const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL
  || (process.env.VERCEL_PROJECT_PRODUCTION_URL ? `https://${process.env.VERCEL_PROJECT_PRODUCTION_URL}` : "http://localhost:3000");

export const metadata: Metadata = {
  metadataBase: new URL(SITE_URL),
  title: { default: `${brand.name} — ${brand.tagline}`, template: `%s — ${brand.name}` },
  description: brand.description,
};

export const viewport: Viewport = {
  themeColor: "#F0F1EC",  // light only: people found the dark theme harder to read, so it was retired
  width: "device-width", initialScale: 1,
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
        {/* eslint-disable-next-line @next/next/no-page-custom-font */}
        <link
          rel="stylesheet"
          href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,600;12..96,700;12..96,800&family=Instrument+Serif:ital@0;1&family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap"
        />
        <link rel="icon" href="/favicon.svg?v=2" type="image/svg+xml" />
      </head>
      <body>
        <div className="page-bg" aria-hidden />
        <SpotlightTracker />
        <AuthProvider>{children}</AuthProvider>
      </body>
    </html>
  );
}
