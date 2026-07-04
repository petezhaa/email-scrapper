"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { ArrowRight, Check, ExternalLink, Loader2, PencilLine, Search, Trash2 } from "lucide-react";
import { api, type Contact } from "@/lib/api";
import { PIPELINE_DONE } from "@/components/job-bar";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

export default function ContactsPage() {
  const router = useRouter();
  const [rows, setRows] = useState<Contact[] | null>(null);
  const [q, setQ] = useState("");
  const [research, setResearch] = useState("");
  const [cat, setCat] = useState<"all" | "research" | "industry">("all");
  const [drafting, setDrafting] = useState(false);

  const load = useCallback(() => {
    api.getContacts().then((d) => setRows(d.rows)).catch(() => setRows([]));
  }, []);

  useEffect(() => {
    load();
    const onDone = () => load();
    window.addEventListener(PIPELINE_DONE, onDone);
    return () => window.removeEventListener(PIPELINE_DONE, onDone);
  }, [load]);

  const filtered = useMemo(() => {
    if (!rows) return [];
    const query = q.trim().toLowerCase();
    const r = research.trim().toLowerCase();
    return rows.filter((row) => {
      const passesCat = cat === "all" || (row.category || "research") === cat;
      const hay = Object.values(row).join(" ").toLowerCase();
      const passesSearch = !query || hay.includes(query);
      const interests = (row.research_interests || "").toLowerCase();
      const passesResearch =
        !r || r.split(/\s+/).every((w) => interests.includes(w));
      return passesCat && passesSearch && passesResearch;
    });
  }, [rows, q, research, cat]);

  const catCounts = useMemo(() => {
    const c = { research: 0, industry: 0 };
    for (const row of rows ?? []) {
      if ((row.category || "research") === "industry") c.industry++;
      else c.research++;
    }
    return c;
  }, [rows]);

  async function generate() {
    setDrafting(true);
    try {
      await api.runDraft();
      router.push("/drafts");
    } catch (e) {
      toast.error(String((e as Error).message ?? e));
      setDrafting(false);
    }
  }

  async function draftOne(row: Contact) {
    try {
      await api.draftOne({
        email: row.email || undefined,
        profile_url: row.profile_url || undefined,
        name: row.name || undefined,
      });
      toast.success(`Drafting an email for ${row.name || "this contact"} — check the Drafts tab.`);
    } catch (e) {
      toast.error(String((e as Error).message ?? e));
    }
  }

  async function remove(row: Contact) {
    setRows((prev) => prev?.filter((x) => x !== row) ?? null);
    try {
      await api.deleteContact(
        row.email ? { email: row.email } : { profile_url: row.profile_url },
      );
    } catch {
      load();
    }
  }

  const total = rows?.length ?? 0;
  const active = q || research;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <h1 className="flex items-baseline gap-2 text-3xl">
          Contacts
          {rows !== null && (
            <span className="font-mono text-base font-normal text-muted-foreground">
              · {total}
            </span>
          )}
        </h1>
        <Button onClick={generate} disabled={drafting || total === 0}>
          {drafting ? (
            <Loader2 className="size-4 animate-spin" />
          ) : (
            <ArrowRight className="size-4" />
          )}
          Generate drafts
        </Button>
      </div>

      {rows === null ? (
        <Skeleton className="h-64 w-full" />
      ) : total === 0 ? (
        <Card className="flex flex-col items-center gap-3 py-16 text-center text-muted-foreground">
          <Search className="size-8 text-muted-foreground/60" strokeWidth={1.5} />
          <p>
            No contacts yet. Go to{" "}
            <a href="/setup" className="text-brand underline underline-offset-2">
              Setup
            </a>
            , add organizations or a search, and start finding contacts.
          </p>
        </Card>
      ) : (
        <>
          <div className="inline-flex rounded-lg border border-border bg-secondary/40 p-0.5 text-sm">
            {(
              [
                ["all", "All", total],
                ["research", "Academia", catCounts.research],
                ["industry", "Industry", catCounts.industry],
              ] as const
            ).map(([key, label, n]) => (
              <button
                key={key}
                type="button"
                onClick={() => setCat(key)}
                className={cn(
                  "flex items-center gap-1.5 rounded-md px-3 py-1.5 font-medium transition-colors",
                  cat === key
                    ? "bg-card text-foreground shadow-sm"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                {label}
                <span className="font-mono text-xs text-muted-foreground">{n}</span>
              </button>
            ))}
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <div className="relative">
              <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="Search all…"
                className="w-64 pl-9"
              />
            </div>
            <Input
              value={research}
              onChange={(e) => setResearch(e.target.value)}
              placeholder="Filter by research interest…"
              className="w-64"
            />
            {active || cat !== "all" ? (
              <span className="font-mono text-xs text-muted-foreground">
                {filtered.length} of {total}
              </span>
            ) : null}
          </div>
          <p className="text-[13px] text-muted-foreground">
            Review and remove anyone irrelevant before generating drafts.
            Generating never overwrites a draft you&rsquo;ve already edited.
          </p>

          <Card className="fade-in overflow-hidden p-0">
            <Table>
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead>Name</TableHead>
                  <TableHead>Email</TableHead>
                  <TableHead>Title / Affiliation</TableHead>
                  <TableHead>Research interests</TableHead>
                  <TableHead className="w-10" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {filtered.map((row, i) => (
                  <TableRow key={row.profile_url || row.email || i}>
                    <TableCell className="align-top font-medium">
                      <div className="flex flex-col gap-1">
                        {row.profile_url ? (
                          <a
                            href={row.profile_url}
                            target="_blank"
                            rel="noopener"
                            className="inline-flex w-fit items-center gap-1 border-b border-brand/40 hover:text-brand hover:border-brand"
                          >
                            {row.name}
                            <ExternalLink className="size-3 opacity-60" />
                          </a>
                        ) : (
                          <span>{row.name}</span>
                        )}
                        <div className="flex flex-wrap items-center gap-1.5">
                          <CategoryBadge category={row.category} />
                          {row.drafted && (
                            <Badge
                              variant="outline"
                              className="w-fit gap-1 border-ok/30 bg-ok-soft font-normal text-ok"
                            >
                              <Check className="size-3" />
                              drafted
                            </Badge>
                          )}
                        </div>
                      </div>
                    </TableCell>
                    <TableCell className="align-top">
                      {row.email ? (
                        <span className="text-sm">{row.email}</span>
                      ) : row.profile_url ? (
                        <AddEmail
                          profileUrl={row.profile_url}
                          onSaved={(email) => {
                            setRows(
                              (prev) =>
                                prev?.map((x) =>
                                  x === row ? { ...x, email } : x,
                                ) ?? null,
                            );
                          }}
                        />
                      ) : (
                        <span className="text-muted-foreground/60">—</span>
                      )}
                    </TableCell>
                    <TableCell className="align-top">
                      <div className="text-sm">{row.title}</div>
                      {row.affiliation && (
                        <div className="mt-0.5 text-xs text-muted-foreground">
                          {row.affiliation}
                        </div>
                      )}
                    </TableCell>
                    <TableCell className="max-w-96 whitespace-normal align-top text-[13px] leading-snug text-muted-foreground">
                      {row.research_interests}
                    </TableCell>
                    <TableCell className="align-top">
                      <div className="flex items-center justify-end gap-1">
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-8 gap-1.5 text-muted-foreground hover:text-foreground"
                          onClick={() => draftOne(row)}
                          title={row.drafted ? "Regenerate the draft for this contact" : "Draft an email for this contact"}
                        >
                          <PencilLine className="size-3.5" />
                          {row.drafted ? "Redraft" : "Draft"}
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="size-8 text-muted-foreground hover:text-destructive"
                          onClick={() => remove(row)}
                          aria-label="Remove contact"
                        >
                          <Trash2 className="size-4" />
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
                {filtered.length === 0 && (
                  <TableRow>
                    <TableCell
                      colSpan={5}
                      className="py-10 text-center text-sm text-muted-foreground"
                    >
                      No contacts match your filters.
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </Card>
        </>
      )}
    </div>
  );
}

function CategoryBadge({ category }: { category: Contact["category"] }) {
  const industry = category === "industry";
  return (
    <Badge
      variant="outline"
      className={cn(
        "w-fit font-normal",
        industry
          ? "border-brand/30 bg-brand-soft text-brand"
          : "border-ok/30 bg-ok-soft text-ok",
      )}
    >
      {industry ? "Industry" : "Academia"}
    </Badge>
  );
}

function AddEmail({
  profileUrl,
  onSaved,
}: {
  profileUrl: string;
  onSaved: (email: string) => void;
}) {
  const [value, setValue] = useState("");
  const [saving, setSaving] = useState(false);

  async function save() {
    const email = value.trim().toLowerCase();
    if (!email.includes("@")) {
      toast.error("Enter a valid email.");
      return;
    }
    setSaving(true);
    try {
      await api.setContactEmail(profileUrl, email);
      onSaved(email);
    } catch (e) {
      toast.error(String((e as Error).message ?? e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="flex items-center gap-1.5">
      <Input
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && save()}
        placeholder="add email"
        className="h-8 w-40 text-[13px]"
      />
      <Button size="sm" variant="ghost" className="h-8 text-brand" onClick={save} disabled={saving}>
        save
      </Button>
    </div>
  );
}
