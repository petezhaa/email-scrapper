"use client";

import { useEffect, useState } from "react";
import { useTheme } from "next-themes";
import { Moon, Sun } from "lucide-react";
import { Button } from "@/components/ui/button";

export function ThemeToggle() {
  const { resolvedTheme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  const isDark = resolvedTheme === "dark";

  function toggle() {
    const root = document.documentElement;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (!reduce) {
      // Enable color transitions only for the duration of the switch.
      root.classList.add("theme-anim");
      window.setTimeout(() => root.classList.remove("theme-anim"), 550);
    }
    setTheme(isDark ? "light" : "dark");
  }

  return (
    <Button
      variant="ghost"
      size="icon"
      aria-label="Toggle light / dark theme"
      onClick={toggle}
      className="size-9 text-muted-foreground hover:text-foreground"
    >
      {/* Keyed so the icon replays its spin-in on each switch. */}
      <span key={mounted ? (isDark ? "moon" : "sun") : "sun"} className="theme-icon">
        {mounted && isDark ? <Moon className="size-4" /> : <Sun className="size-4" />}
      </span>
    </Button>
  );
}
