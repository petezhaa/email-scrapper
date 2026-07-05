"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { toast } from "sonner";
import { Loader2, Send, Reply } from "lucide-react";
import { api, type SentEmail } from "@/lib/api";
import { PIPELINE_DONE } from "@/components/job-bar";
import { BackendDownBanner } from "@/components/backend-down";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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

function whenLabel(iso: string): string {
  // sent_at_utc is an ISO string; render a compact local date-time.
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

const WEEK_MS = 7 * 24 * 60 * 60 * 1000;

function olderThanWeek(iso: string): boolean {
  const d = new Date(iso);
  return !isNaN(d.getTime()) && Date.now() - d.getTime() > WEEK_MS;
}

export default function SentPage() {
  const [rows, setRows] = useState<SentEmail[] | null>(null);
  const [following, setFollowing] = useState<string | null>(null);
  const [down, setDown] = useState(false);

  const load = useCallback(() => {
    api
      .getSent()
      .then((d) => {
        setDown(false);
        setRows(d.rows);
      })
      .catch((e) => {
        if ((e as Error).name === "BackendDown") setDown(true);
        else setRows([]);
      });
  }, []);

  useEffect(() => {
    load();
    const onDone = () => load();
    window.addEventListener(PIPELINE_DONE, onDone);
    return () => window.removeEventListener(PIPELINE_DONE, onDone);
  }, [load]);

  async function followUp(row: SentEmail) {
    setFollowing(row.to + row.subject);
    try {
      const r = await api.followUp({ to: row.to, name: row.name, subject: row.subject });
      if (r.already_running) {
        toast.info("A follow-up is already being drafted — wait for it to finish first.");
      } else {
        toast.success(`Drafting a follow-up to ${row.name || row.to} — check the Drafts tab.`);
      }
    } catch (e) {
      toast.error(String((e as Error).message ?? e));
    } finally {
      setFollowing(null);
    }
  }

  const total = rows?.length ?? 0;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-baseline justify-between gap-4">
        <h1 className="flex items-baseline gap-2 text-3xl">
          Sent
          {rows !== null && (
            <span className="font-mono text-base font-normal text-muted-foreground">
              · {total}
            </span>
          )}
        </h1>
      </div>

      {down ? (
        <BackendDownBanner onRetry={load} />
      ) : rows === null ? (
        <Skeleton className="h-56 w-full" />
      ) : total === 0 ? (
        <Card className="flex flex-col items-center gap-3 py-16 text-center text-muted-foreground">
          <Send className="size-8 text-muted-foreground/60" strokeWidth={1.5} />
          <p>
            Nothing sent yet. Approve drafts on{" "}
            <Link href="/drafts" className="text-brand underline underline-offset-2">
              Drafts
            </Link>{" "}
            and hit Send approved — they&rsquo;ll show up here.
          </p>
        </Card>
      ) : (
        <>
          <p className="text-[13px] text-muted-foreground">
            Emails you&rsquo;ve sent, newest first. No reply after a week? Draft a short
            follow-up — it lands in Drafts for you to review before it goes out.
          </p>
          <Card className="fade-in overflow-hidden p-0">
            <Table>
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead>Sent</TableHead>
                  <TableHead>To</TableHead>
                  <TableHead>Subject</TableHead>
                  <TableHead className="w-10" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((row, i) => (
                  <TableRow key={row.to + row.subject + i}>
                    <TableCell className="whitespace-nowrap align-top font-mono text-xs text-muted-foreground">
                      <div>{whenLabel(row.sent_at_utc)}</div>
                      {olderThanWeek(row.sent_at_utc) && (
                        <Badge className="mt-1.5 bg-warn-soft font-sans text-warn">
                          7d+ — good time to follow up
                        </Badge>
                      )}
                    </TableCell>
                    <TableCell className="align-top">
                      <div className="font-medium">{row.name || "—"}</div>
                      <div className="text-xs text-muted-foreground">{row.to}</div>
                    </TableCell>
                    <TableCell className="align-top text-[13px]">{row.subject}</TableCell>
                    <TableCell className="align-top">
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-8 gap-1.5 text-muted-foreground hover:text-foreground"
                        onClick={() => followUp(row)}
                        disabled={following === row.to + row.subject}
                        title="Draft a short follow-up to this person"
                      >
                        {following === row.to + row.subject ? (
                          <Loader2 className="size-3.5 animate-spin" />
                        ) : (
                          <Reply className="size-3.5" />
                        )}
                        Follow up
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Card>
        </>
      )}
    </div>
  );
}
