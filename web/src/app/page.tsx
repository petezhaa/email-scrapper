"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  ArrowRight,
  Check,
  Circle,
  GraduationCap,
  Building2,
  Users,
  PencilLine,
  Send,
} from "lucide-react";
import { api, type Contact, type SentEmail } from "@/lib/api";
import { PIPELINE_DONE } from "@/components/job-bar";
import { BackendDownBanner } from "@/components/backend-down";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

type Data = {
  contacts: Contact[];
  counts: Record<string, number>;
  sent: SentEmail[];
  ready: { name: boolean; gmail: boolean; resume: boolean; apiKey: boolean };
};

export default function HomePage() {
  const [d, setD] = useState<Data | null>(null);
  const [down, setDown] = useState(false);

  const load = useCallback(() => {
    Promise.all([api.getState(), api.getContacts(), api.getDrafts(), api.getSent()])
      .then(([s, c, dr, se]) => {
        setDown(false);
        setD({
          contacts: c.rows,
          counts: dr.counts,
          sent: se.rows,
          ready: {
            name: !!s.name.trim(),
            gmail: !!s.gmail_address.trim() && s.gmail_app_password_set,
            resume: s.resume_ok,
            apiKey: s.api_key_ok,
          },
        });
      })
      .catch((e) => {
        if ((e as Error).name === "BackendDown") setDown(true);
        else setD({ contacts: [], counts: {}, sent: [], ready: { name: false, gmail: false, resume: false, apiKey: false } });
      });
  }, []);

  useEffect(() => {
    load();
    window.addEventListener(PIPELINE_DONE, load);
    return () => window.removeEventListener(PIPELINE_DONE, load);
  }, [load]);

  if (down) {
    return <BackendDownBanner onRetry={load} />;
  }

  if (!d) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-10 w-72" />
        <div className="grid gap-4 sm:grid-cols-3">
          <Skeleton className="h-28" />
          <Skeleton className="h-28" />
          <Skeleton className="h-28" />
        </div>
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }

  const academia = d.contacts.filter((c) => (c.category || "research") !== "industry").length;
  const industry = d.contacts.length - academia;
  const approved = d.counts["approved"] ?? 0;
  const pending = d.counts["pending"] ?? 0;
  const draftTotal = Object.values(d.counts).reduce((a, b) => a + b, 0);
  const allReady = Object.values(d.ready).every(Boolean);

  // The single most useful next action.
  const next = !allReady
    ? { href: "/setup", label: "Finish setup", note: "Add the missing pieces below to start." }
    : d.contacts.length === 0
      ? { href: "/find", label: "Find contacts", note: "Search Academia or Industry for people to reach out to." }
      : draftTotal === 0
        ? { href: "/contacts", label: "Generate drafts", note: `${d.contacts.length} contact${d.contacts.length === 1 ? "" : "s"} ready — write their emails.` }
        : approved > 0
          ? { href: "/drafts", label: `Send ${approved} approved`, note: "Approved drafts are queued to send." }
          : pending > 0
            ? { href: "/drafts", label: `Review ${pending} draft${pending === 1 ? "" : "s"}`, note: "Approve the ones you like, then send." }
            : { href: "/find", label: "Find more contacts", note: "You're all caught up." };

  return (
    <div className="space-y-8">
      <header className="space-y-2">
        <h1 className="text-3xl">Overview</h1>
        <p className="text-[15px] text-muted-foreground">
          Your outreach pipeline at a glance.
        </p>
      </header>

      {/* Stat tiles */}
      <div className="grid gap-4 sm:grid-cols-3">
        <StatTile
          href="/contacts"
          icon={<Users className="size-4" />}
          label="Contacts"
          value={d.contacts.length}
          sub={
            d.contacts.length ? (
              <span className="flex items-center gap-2">
                <span className="inline-flex items-center gap-1">
                  <GraduationCap className="size-3" /> {academia}
                </span>
                <span className="inline-flex items-center gap-1">
                  <Building2 className="size-3" /> {industry}
                </span>
              </span>
            ) : (
              "none yet"
            )
          }
        />
        <StatTile
          href="/drafts"
          icon={<PencilLine className="size-4" />}
          label="Drafts"
          value={draftTotal}
          sub={
            draftTotal
              ? `${approved} approved · ${pending} pending`
              : "none yet"
          }
        />
        <StatTile
          href="/sent"
          icon={<Send className="size-4" />}
          label="Sent"
          value={d.sent.length}
          sub={d.sent.length ? "emails out the door" : "none yet"}
        />
      </div>

      {/* Next step */}
      <Card className="ring-brand/30">
        <CardContent className="flex flex-wrap items-center justify-between gap-4 py-5">
          <div>
            <p className="eyebrow">Next step</p>
            <p className="mt-1 text-[15px] text-muted-foreground">{next.note}</p>
          </div>
          <Button render={<Link href={next.href} />}>
            {next.label}
            <ArrowRight className="size-4" />
          </Button>
        </CardContent>
      </Card>

      {/* Readiness */}
      <div>
        <p className="eyebrow mb-3">Setup</p>
        <div className="grid gap-2 sm:grid-cols-2">
          <ReadyRow ok={d.ready.name} label="Your name" href="/setup" />
          <ReadyRow ok={d.ready.gmail} label="Gmail + app password" href="/setup" />
          <ReadyRow ok={d.ready.resume} label="Resume uploaded" href="/setup" />
          <ReadyRow ok={d.ready.apiKey} label="Anthropic API key" href="/setup" />
        </div>
      </div>
    </div>
  );
}

function StatTile({
  href,
  icon,
  label,
  value,
  sub,
}: {
  href: string;
  icon: React.ReactNode;
  label: string;
  value: number;
  sub: React.ReactNode;
}) {
  return (
    <Link href={href}>
      <Card className="transition-all hover:-translate-y-0.5 hover:ring-brand/40">
        <CardContent className="py-5">
          <div className="flex items-center gap-2 text-muted-foreground">
            {icon}
            <span className="text-[13px] font-medium">{label}</span>
          </div>
          <div className="mt-2 font-display text-4xl leading-none">{value}</div>
          <div className="mt-1.5 text-xs text-muted-foreground">{sub}</div>
        </CardContent>
      </Card>
    </Link>
  );
}

function ReadyRow({ ok, label, href }: { ok: boolean; label: string; href: string }) {
  return (
    <Link
      href={href}
      className="flex items-center gap-2.5 rounded-lg border border-border bg-card px-3.5 py-2.5 text-sm transition-colors hover:border-brand/40"
    >
      <span
        className={cn(
          "grid size-5 shrink-0 place-items-center rounded-full",
          ok ? "bg-ok-soft text-ok" : "bg-secondary text-muted-foreground",
        )}
      >
        {ok ? <Check className="size-3.5" /> : <Circle className="size-2.5" />}
      </span>
      <span className={ok ? "" : "text-muted-foreground"}>{label}</span>
    </Link>
  );
}
