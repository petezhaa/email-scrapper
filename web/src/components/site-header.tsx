"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Mail } from "lucide-react";
import { cn } from "@/lib/utils";
import { ThemeToggle } from "@/components/theme-toggle";

const NAV = [
  { href: "/setup", num: "01", label: "Setup" },
  { href: "/find", num: "02", label: "Find" },
  { href: "/contacts", num: "03", label: "Contacts" },
  { href: "/drafts", num: "04", label: "Drafts" },
  { href: "/sent", num: "05", label: "Sent" },
];

export function SiteHeader() {
  const pathname = usePathname();
  return (
    <header className="sticky top-0 z-20 border-b border-border/80 bg-background/80 backdrop-blur-md supports-[backdrop-filter]:bg-background/70">
      <div className="mx-auto flex w-full max-w-5xl items-stretch gap-8 px-6">
        <Link href="/" className="flex items-center gap-3 py-3.5">
          <span className="grid size-8 place-items-center rounded-lg bg-primary text-primary-foreground">
            <Mail className="size-4" strokeWidth={1.75} />
          </span>
          <span className="font-display text-lg tracking-tight">
            Research&nbsp;Outreach
          </span>
        </Link>

        <nav className="ml-auto flex items-stretch gap-7">
          {NAV.map((item) => {
            const active = pathname === item.href || pathname.startsWith(item.href + "/");
            return (
              <Link
                key={item.href}
                href={item.href}
                className={cn(
                  "group flex items-baseline gap-1.5 border-b-2 border-transparent pb-3.5 pt-4 text-sm font-medium transition-colors",
                  active
                    ? "border-brand text-foreground"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                <span
                  className={cn(
                    "font-mono text-[11px]",
                    active ? "text-brand" : "text-muted-foreground/70",
                  )}
                >
                  {item.num}
                </span>
                {item.label}
              </Link>
            );
          })}
        </nav>
        <div className="flex items-center pl-1">
          <ThemeToggle />
        </div>
      </div>
    </header>
  );
}
