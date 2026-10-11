"use client";

export default function GlobalError({ reset }: { error: Error; reset: () => void }) {
  return (
    <main className="mx-auto max-w-lg px-5 py-24 text-center">
      <h1 className="text-2xl font-bold">Oops. That wasn&apos;t meant to happen.</h1>
      <p className="mt-2 text-ink-soft">Something broke on our side. Your trips are safe.</p>
      <button className="btn-dark mt-6" onClick={reset}>Try again</button>
    </main>
  );
}
