"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowRight, Search } from "lucide-react";

// What people actually type: India and abroad, English and Hinglish, with and without a budget.
const EXAMPLES = [
  "Goa for 3 days under ₹20,000",
  "Weekend in Dubai for two",
  "Delhi to Agra this Saturday, veg",
  "Manali 5 din 25k me with friends",
  "Paris for 4 days, 1,500 euros",
  "Jaipur to Udaipur by train, family of 4",
];

/** The hero's one box: describe the trip in a sentence and land in the planner with it filled in (see
 * lib/tripQuery.ts). An empty box plans the example currently shown, so the button always does something. */
export function HeroSearch() {
  const router = useRouter();
  const [text, setText] = useState("");
  const [i, setI] = useState(0);
  const [focused, setFocused] = useState(false);

  useEffect(() => {
    if (text) return;
    const t = setInterval(() => setI((n) => (n + 1) % EXAMPLES.length), focused ? 4200 : 2600);
    return () => clearInterval(t);
  }, [text, focused]);

  function plan(e: React.FormEvent) {
    e.preventDefault();
    const q = (text.trim() || EXAMPLES[i]).slice(0, 300);
    router.push(`/plan?q=${encodeURIComponent(q)}`);
  }

  return (
    <form onSubmit={plan} role="search" aria-label="Describe your trip"
          className="spotlight group/s relative flex w-full max-w-[560px] items-center gap-2 rounded-2xl border border-line bg-paper/80 p-1.5 pl-4 shadow-[0_24px_60px_-30px_rgb(var(--shadow)/0.9)] backdrop-blur-xl transition-colors focus-within:border-signal/60">
      <Search className="h-5 w-5 shrink-0 text-ink-mute transition-colors group-focus-within/s:text-signal" aria-hidden />
      <div className="relative min-w-0 flex-1">
        <input value={text} onChange={(e) => setText(e.target.value)} onFocus={() => setFocused(true)} onBlur={() => setFocused(false)}
               aria-label="Describe your trip, for example: Goa for 3 days under 20,000 rupees" autoComplete="off" enterKeyHint="go"
               className="w-full bg-transparent py-3 text-[16px] text-ink placeholder:text-transparent focus:outline-none" placeholder="Describe your trip" />
        {!text && (
          <span key={i} aria-hidden className="fade-up pointer-events-none absolute inset-y-0 left-0 flex max-w-full items-center truncate text-[16px] text-ink-mute">
            {EXAMPLES[i]}
          </span>
        )}
      </div>
      <button type="submit" className="btn-primary shine shrink-0 !rounded-xl !px-4 !py-3 sm:!px-5">
        <span className="hidden sm:inline">Build it</span><ArrowRight className="h-4 w-4" aria-hidden />
        <span className="sr-only sm:hidden">Build it</span>
      </button>
    </form>
  );
}
