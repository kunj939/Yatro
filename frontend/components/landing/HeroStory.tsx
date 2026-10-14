"use client";

import { useEffect, useState } from "react";

type Story = {
  label: string;      // what the dot is called
  tag: string;        // the small pill above the headline
  lead: string;       // headline, up to the accent
  accent: string;     // the italic, coloured part
  tone: string;       // accent colour: darker on the light theme, brighter on the dark one
  body: React.ReactNode;
  quote: string;
};

// The same product, told to seven kinds of traveller. Each swaps in for the last; the first is the default
// (and what shows before scripts run).
const STORIES: Story[] = [
  {
    label: "Everyone", tag: "Less scrolling. More going.",
    lead: "Your budget called. It wants", accent: "a vacation.",
    tone: "highlight",
    body: <>Tell us the money and the place. We plan the <span className="font-semibold text-ink">rides, stays, food and fun</span>, with real timings and prices that fit.</>,
    quote: "Close the tabs. Open the world.",
  },
  {
    label: "Students", tag: "For students · backpackers · friends",
    lead: "Broke Today.", accent: "Abroad Tomorrow.",
    tone: "text-[rgb(77_124_15)] dark:text-[rgb(190_255_74)]",
    body: <>Big dreams, very <span className="font-semibold text-ink">specific bank balances</span>. Cheap trains, cheaper stays, street food that slaps.</>,
    quote: "Attendance can wait. These memories can't.",
  },
  {
    label: "Professionals", tag: "For professionals · weekend escapes",
    lead: "Out of Office.", accent: "Out of This World.",
    tone: "text-[rgb(2_132_199)] dark:text-[rgb(125_200_255)]",
    body: <>Meetings all week. <span className="font-semibold text-ink">Mountains this weekend.</span> A plan that fits between EOD and Monday&apos;s standup.</>,
    quote: "Your inbox can survive without you.",
  },
  {
    label: "Couples", tag: "For couples · honeymoons",
    lead: "Two Hearts.", accent: "One Escape Plan.",
    tone: "text-[rgb(219_39_119)] dark:text-[rgb(255_143_190)]",
    body: <>Date nights are great. <span className="font-semibold text-ink">New cities together are better.</span> Sunsets, stays and dinners on one budget.</>,
    quote: "Fall in love somewhere new.",
  },
  {
    label: "Families", tag: "For families · grandparents · kids",
    lead: "Different Generations.", accent: "One Beautiful Trip.",
    tone: "text-[rgb(180_83_9)] dark:text-[rgb(255_196_110)]",
    body: <>Slow mornings for Dadi, everything for the kids. <span className="font-semibold text-ink">One day that works for every age.</span></>,
    quote: "The best family stories happen together.",
  },
  {
    label: "Seniors", tag: "For retirees · slow travel",
    lead: "The World Isn't", accent: "Retiring Yet.",
    tone: "text-[rgb(15_118_110)] dark:text-[rgb(110_231_200)]",
    body: <>You&apos;ve earned the window seat. Culture, comfy stays, short walks and <span className="font-semibold text-ink">no exhausting schedules</span>.</>,
    quote: "You've earned the time. Now take the trip.",
  },
  {
    label: "Solo", tag: "For solo travellers · nomads",
    lead: "Just You.", accent: "Zero Group Chat Drama.",
    tone: "text-[rgb(109_40_217)] dark:text-[rgb(196_167_255)]",
    body: <>No &ldquo;who&apos;s booking?&rdquo;, no &ldquo;I&apos;m out&rdquo; at 11 pm. <span className="font-semibold text-ink">Your pace, your budget, your playlist.</span></>,
    quote: "Main character. Solo season.",
  },
];

const EVERY_MS = 23_000; // how long each story stays up

/** The hero's headline block. It turns through the stories above on its own, pauses while you hover it, and
 * the dots jump to one. All the stories sit in one grid cell so the block is as tall as the tallest and nothing below
 * it moves. The real <h1> is visually hidden: the headline you see changes, the page's title doesn't. */
export function HeroStory() {
  const [i, setI] = useState(0);
  const [paused, setPaused] = useState(false);

  // Reduced motion does not stop the turning: the swap is a fade (globals.css makes it instant there), not movement,
  // and stopping it made the headline look stuck on machines that have animations switched off.
  useEffect(() => {
    if (paused) return;
    const t = setTimeout(() => setI((n) => (n + 1) % STORIES.length), EVERY_MS);
    return () => clearTimeout(t); // a click on a dot restarts the wait
  }, [i, paused]);

  return (
    <div onMouseEnter={() => setPaused(true)} onMouseLeave={() => setPaused(false)}
         onFocus={() => setPaused(true)} onBlur={() => setPaused(false)}>
      <h1 className="sr-only">Yaitro: trips planned around your budget</h1>
      <div className="relative">
      <div className="grid">
        {STORIES.map((s, n) => {
          const on = n === i;
          return (
            <div key={s.label} aria-hidden={!on || undefined}
                 className={`col-start-1 row-start-1 min-w-0 transition-all duration-500 ease-out ${
                   on ? "translate-y-0 opacity-100" : "pointer-events-none translate-y-2 opacity-0"}`}>
              <p className="max-w-[22ch] text-[clamp(2rem,3.5vw,2.9rem)] font-extrabold leading-[1.06] text-ink">
                {s.lead}{" "}
                <span className={`accent ${s.tone === "highlight" ? "highlight" : s.tone} whitespace-nowrap`}>{s.accent}</span>
              </p>
              <p className="mt-5 max-w-[46ch] text-[17px] leading-relaxed text-ink-soft">{s.body}</p>
            </div>
          );
        })}
      </div>
      </div>
    </div>
  );
}
