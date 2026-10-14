import type { Config } from "tailwindcss";

/* Yaitro — Modern Transit.
 *
 * Every colour is a CSS variable (see app/globals.css), so light and dark are one definition swapped at the
 * root rather than a `dark:` variant on every element.
 *
 * One rule governs the palette: `signal` (the brand blue) never carries meaning about data. Provenance is
 * always told by teal (verified), caution (needs check), haze-2 (estimated / not available) and demo (sample),
 * so a traveller never has to wonder whether a blue thing is "confirmed". Brand is brand; evidence is evidence.
 */
const v = (name: string) => `rgb(var(--${name}) / <alpha-value>)`;

const config: Config = {
  darkMode: "class",
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        paper: v("paper"),                                                     // cards and raised surfaces
        night: { DEFAULT: v("night"), 2: v("night-2"), 3: v("night-3") },      // deliberately dark surfaces
        ink: { DEFAULT: v("ink"), soft: v("ink-soft"), mute: v("ink-mute") },
        haze: { DEFAULT: v("haze"), 2: v("haze-2") },                          // page and recessed fills
        line: v("line"),
        signal: { DEFAULT: v("signal"), deep: v("signal-deep"), soft: v("signal-soft") },  // brand only
        spark: v("spark"),
        "on-signal": v("on-signal"),                                                                 // brand highlight
        teal: { DEFAULT: v("teal"), deep: v("teal-deep"), soft: v("teal-soft") },          // verified
        caution: { DEFAULT: v("caution"), soft: v("caution-soft") },                       // needs check
        demo: { DEFAULT: v("demo"), soft: v("demo-soft") },                                // sample data
        danger: { DEFAULT: v("danger"), soft: v("danger-soft") },
      },
      fontFamily: {
        sans: ["Inter", '"Segoe UI"', "system-ui", "-apple-system", "sans-serif"],
        display: ['"Inter Tight"', "Inter", "system-ui", "sans-serif"],
      },
      fontSize: {
        display: ["clamp(2.35rem, 4.4vw, 3.5rem)", { lineHeight: "1.04", letterSpacing: "-0.03em" }],
        title: ["clamp(1.9rem, 3.6vw, 2.85rem)", { lineHeight: "1.06", letterSpacing: "-0.022em" }],
      },
      borderRadius: { ticket: "14px" },
      boxShadow: {
        lift: "0 1px 0 rgb(var(--shadow) / 0.05), 0 14px 36px -22px rgb(var(--shadow) / 0.42)",
        glow: "0 0 0 1px rgb(var(--signal) / 0.28), 0 20px 56px -24px rgb(var(--signal) / 0.45)",
      },
    },
  },
  plugins: [],
};
export default config;