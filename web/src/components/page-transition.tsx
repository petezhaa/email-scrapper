"use client";

import { usePathname } from "next/navigation";

// Re-keys on every route change so the incoming page replays its entrance
// animation — a quick fade + slide, like turning to the next page. The CSS
// (.page-enter in globals.css) honors prefers-reduced-motion.
export function PageTransition({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  return (
    <div key={pathname} className="page-enter">
      {children}
    </div>
  );
}
