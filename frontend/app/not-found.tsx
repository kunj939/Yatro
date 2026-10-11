import Link from "next/link";
import { Navbar } from "@/components/Navbar";

export default function NotFound() {
  return (
    <main className="min-h-screen">
      <Navbar />
      <div className="mx-auto max-w-lg px-5 py-24 text-center">
        <p className="num text-6xl font-bold text-signal-deep">404</p>
        <h1 className="mt-3 text-2xl font-bold">This page took a wrong turn</h1>
        <p className="mt-2 text-ink-soft">Nothing here. The link might be old, or there's a typo.</p>
        <div className="mt-6 flex justify-center gap-3">
          <Link href="/" className="btn-dark">Go home</Link>
          <Link href="/plan" className="btn-ghost">Plan my trip</Link>
        </div>
      </div>
    </main>
  );
}