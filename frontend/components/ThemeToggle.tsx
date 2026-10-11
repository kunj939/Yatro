"use client";

import { useEffect, useState } from "react";
import { Moon, Sun } from "lucide-react";

const KEY = "yatro.theme";

/** Light/dark switch. The choice is remembered; with no choice saved the system setting wins. */
export function ThemeToggle({ className = "" }: { className?: string }) {
  const [dark, setDark] = useState(false);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    setDark(document.documentElement.classList.contains("dark"));
    setReady(true);
  }, []);

  function toggle() {
    const next = !dark;
    setDark(next);
    document.documentElement.classList.toggle("dark", next);
    try {
      localStorage.setItem(KEY, next ? "dark" : "light");
    } catch {
      // private browsing: the page still switches, it just won't be remembered
    }
  }

  return (
    <button type="button" onClick={toggle} aria-pressed={dark}
            aria-label={dark ? "Switch to light theme" : "Switch to dark theme"}
            className={`inline-grid h-9 w-9 place-items-center rounded-lg text-ink-soft transition-colors hover:bg-haze-2 hover:text-ink ${className}`}>
      {ready && dark ? <Moon className="h-[18px] w-[18px]" /> : <Sun className="h-[18px] w-[18px]" />}
    </button>
  );
}

/** Runs before paint so the first frame is already the right theme. */
export const themeScript = `(function(){try{var s=localStorage.getItem("${KEY}");var d=s?s==="dark":matchMedia("(prefers-color-scheme: dark)").matches;if(d)document.documentElement.classList.add("dark")}catch(e){}})()`;