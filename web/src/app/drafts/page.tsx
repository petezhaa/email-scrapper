"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { CheckCheck, Eye, Loader2, PencilLine, RefreshCw, Save, Send, Trash2 } from "lucide-react";
import {
  api,
  previewEmail,
  sendApproved,
  type Draft,
  type DraftStatus,
} from "@/lib/api";
import { PIPELINE_DONE } from "@/components/job-bar";
import { BackendDownBanner } from "@/components/backend-down";
import { useRunningJob } from "@/lib/use-jobs";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";

const STATUSES: DraftStatus[] = ["pending", "approved", "skip", "sent"];

const STATUS_STYLES: Record<DraftStatus, string> = {
  pending: "bg-secondary text-muted-foreground border-border",
  approved: "bg-ok-soft text-ok border-ok/30",
  skip: "bg-secondary text-muted-foreground border-border opacity-80",
  sent: "bg-brand-soft text-brand border-brand/30",
};

const RAIL: Record<DraftStatus, string> = {
  pending: "bg-input",
  approved: "bg-ok",
  skip: "bg-muted-foreground/50",
  sent: "bg-brand",
};

// What each card exposes to the page for keyboard-driven review.
type DraftCardControls = {
  setStatus: (s: DraftStatus) => void;
  save: () => void;
  scrollTo: () => void;
};

export default function DraftsPage() {
  const [items, setItems] = useState<Draft[] | null>(null);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [sending, setSending] = useState(false);
  const [approvingAll, setApprovingAll] = useState(false);
  const [backendDown, setBackendDown] = useState(false);
  const [statusFilter, setStatusFilter] = useState<"all" | DraftStatus>("all");
  const [highlight, setHighlight] = useState<string | null>(null);
  const controls = useRef(new Map<string, DraftCardControls>());
  const runningDraft = useRunningJob(["draft"]);

  const load = useCallback(() => {
    api
      .getDrafts()
      .then((d) => {
        setBackendDown(false);
        setItems(d.items);
        setCounts(d.counts);
      })
      .catch((e) => {
        if ((e as Error).name === "BackendDown") setBackendDown(true);
        else setItems([]);
      });
  }, []);

  useEffect(() => {
    load();
    const onDone = () => load();
    window.addEventListener(PIPELINE_DONE, onDone);
    return () => window.removeEventListener(PIPELINE_DONE, onDone);
  }, [load]);

  async function send() {
    setSending(true);
    try {
      const { count } = await sendApproved();
      toast.success(`Sending ${count} approved email${count > 1 ? "s" : ""}…`);
    } catch (e) {
      toast.error(String((e as Error).message ?? e));
    } finally {
      setSending(false);
    }
  }

  async function approveAll() {
    const pending = (items ?? []).filter((x) => x.status === "pending");
    if (!pending.length) return;
    setApprovingAll(true);
    try {
      // Sequential keeps the tiny local API happy.
      for (const x of pending) {
        await api.saveDraft({ ...x, status: "approved" });
      }
      toast.success(`Approved ${pending.length} draft${pending.length === 1 ? "" : "s"}.`);
    } catch (e) {
      toast.error(String((e as Error).message ?? e));
    } finally {
      setApprovingAll(false);
      load();
    }
  }

  // Optimistic delete: remove the card now, only hit the API once the undo
  // window has passed. Undo re-inserts the card where it was.
  function removeDraft(target: Draft) {
    const idx = items?.findIndex((x) => x.slug === target.slug) ?? -1;
    setItems((prev) => prev?.filter((x) => x.slug !== target.slug) ?? null);
    const timer = setTimeout(() => {
      api
        .deleteDraft(target.slug)
        .then(() => load())
        .catch((e) => {
          toast.error(String((e as Error).message ?? e));
          load();
        });
    }, 5000);
    toast("Draft deleted.", {
      duration: 5000,
      action: {
        label: "Undo",
        onClick: () => {
          clearTimeout(timer);
          setItems((prev) => {
            if (!prev) return prev;
            const next = [...prev];
            next.splice(idx < 0 ? next.length : Math.min(idx, next.length), 0, target);
            return next;
          });
        },
      },
    });
  }

  const visible = useMemo(() => {
    if (!items) return [];
    return statusFilter === "all"
      ? items
      : items.filter((x) => x.status === statusFilter);
  }, [items, statusFilter]);

  // Review shortcuts — inert while typing in a field or inside a dialog.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = document.activeElement as HTMLElement | null;
      if (
        el &&
        (el.tagName === "INPUT" ||
          el.tagName === "TEXTAREA" ||
          el.tagName === "SELECT" ||
          el.isContentEditable ||
          el.closest('[role="dialog"]'))
      )
        return;
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
        if (highlight) {
          e.preventDefault();
          controls.current.get(highlight)?.save();
        }
        return;
      }
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      const key = e.key.toLowerCase();
      if (key === "j" || key === "k") {
        if (!visible.length) return;
        e.preventDefault();
        const slugs = visible.map((x) => x.slug);
        const idx = highlight ? slugs.indexOf(highlight) : -1;
        const next =
          key === "j" ? Math.min(idx + 1, slugs.length - 1) : Math.max(idx - 1, 0);
        setHighlight(slugs[next]);
        controls.current.get(slugs[next])?.scrollTo();
      } else if (key === "a" && highlight) {
        controls.current.get(highlight)?.setStatus("approved");
      } else if (key === "s" && highlight) {
        controls.current.get(highlight)?.setStatus("skip");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [visible, highlight]);

  const total = items?.length ?? 0;
  const approvedCount = counts["approved"] ?? 0;
  const pendingCount = counts["pending"] ?? 0;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <h1 className="flex items-baseline gap-2 text-3xl">
          Drafts
          {items !== null && (
            <span className="font-mono text-base font-normal text-muted-foreground">
              · {total}
            </span>
          )}
        </h1>
        <div className="flex flex-wrap items-center gap-2">
          {STATUSES.filter((s) => counts[s]).map((s) => (
            <Badge
              key={s}
              variant="outline"
              className={cn("font-mono", STATUS_STYLES[s])}
            >
              {s}: {counts[s]}
            </Badge>
          ))}
          <Dialog>
            <DialogTrigger
              render={
                <Button
                  variant="outline"
                  disabled={approvingAll || pendingCount === 0}
                >
                  {approvingAll ? (
                    <Loader2 className="size-4 animate-spin" />
                  ) : (
                    <CheckCheck className="size-4" />
                  )}
                  Approve all pending
                </Button>
              }
            />
            <DialogContent>
              <DialogHeader>
                <DialogTitle>
                  Approve all {pendingCount} pending draft
                  {pendingCount === 1 ? "" : "s"}?
                </DialogTitle>
                <DialogDescription>
                  Every draft still marked <b>pending</b> becomes{" "}
                  <b>approved</b>. Nothing is sent until you click Send
                  approved.
                </DialogDescription>
              </DialogHeader>
              <DialogFooter>
                <DialogClose render={<Button variant="outline" />}>
                  Cancel
                </DialogClose>
                <DialogClose render={<Button onClick={approveAll} />}>
                  <CheckCheck className="size-4" />
                  Approve all
                </DialogClose>
              </DialogFooter>
            </DialogContent>
          </Dialog>
          <Dialog>
            <DialogTrigger
              render={
                <Button disabled={sending || approvedCount === 0}>
                  {sending ? (
                    <Loader2 className="size-4 animate-spin" />
                  ) : (
                    <Send className="size-4" />
                  )}
                  Send approved
                </Button>
              }
            />
            <DialogContent>
              <DialogHeader>
                <DialogTitle>
                  Send {approvedCount} approved email
                  {approvedCount === 1 ? "" : "s"}?
                </DialogTitle>
                <DialogDescription>
                  These go out from your Gmail right now, each with your resume
                  attached. This can&rsquo;t be undone. Anything not marked{" "}
                  <b>approved</b> is left alone.
                </DialogDescription>
              </DialogHeader>
              <DialogFooter>
                <DialogClose render={<Button variant="outline" />}>
                  Cancel
                </DialogClose>
                <DialogClose render={<Button onClick={send} />}>
                  <Send className="size-4" />
                  Send now
                </DialogClose>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        </div>
      </div>

      {items !== null && total > 0 && (
        <p className="text-xs text-muted-foreground">
          Keyboard: <span className="font-mono">j</span>/
          <span className="font-mono">k</span> select ·{" "}
          <span className="font-mono">a</span> approve ·{" "}
          <span className="font-mono">s</span> skip ·{" "}
          <span className="font-mono">Ctrl/⌘+Enter</span> save
        </p>
      )}

      {backendDown ? (
        <BackendDownBanner onRetry={load} />
      ) : items === null ? (
        <div className="space-y-4">
          <Skeleton className="h-56 w-full" />
          <Skeleton className="h-56 w-full" />
        </div>
      ) : total === 0 ? (
        runningDraft ? (
          <Card className="flex flex-col items-center gap-3 py-16 text-center text-muted-foreground">
            <Loader2 className="size-6 animate-spin text-muted-foreground/60" />
            <p>
              Generating drafts —{" "}
              <span className="font-mono text-[13px]">
                {runningDraft.last || "working…"}
              </span>
            </p>
          </Card>
        ) : (
          <Card className="flex flex-col items-center gap-3 py-16 text-center text-muted-foreground">
            <PencilLine className="size-8 text-muted-foreground/60" strokeWidth={1.5} />
            <p>
              No drafts yet. Go to{" "}
              <a href="/contacts" className="text-brand underline underline-offset-2">
                Contacts
              </a>{" "}
              and click &ldquo;Generate drafts&rdquo;.
            </p>
          </Card>
        )
      ) : (
        <div className="fade-in space-y-6">
          <div className="inline-flex rounded-lg border border-border bg-secondary/40 p-0.5 text-sm">
            {(["all", ...STATUSES] as const).map((key) => (
              <button
                key={key}
                type="button"
                onClick={() => setStatusFilter(key)}
                className={cn(
                  "flex items-center gap-1.5 rounded-md px-3 py-1.5 font-medium transition-colors",
                  statusFilter === key
                    ? "bg-card text-foreground shadow-sm"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                {key === "all" ? "All" : key}
                <span className="font-mono text-xs text-muted-foreground">
                  {key === "all" ? total : counts[key] ?? 0}
                </span>
              </button>
            ))}
          </div>
          <p className="text-[13px] text-muted-foreground">
            Edit anything. Set status to <b>approved</b> to queue for sending, or{" "}
            <b>skip</b> to ignore. Sent emails are marked <b>sent</b>{" "}
            and won&rsquo;t resend. Preview shows the real React Email HTML.
          </p>
          {visible.map((d) => (
            <DraftCard
              key={d.slug}
              draft={d}
              highlighted={d.slug === highlight}
              onSaved={() => load()}
              onDelete={() => removeDraft(d)}
              register={(slug, c) => {
                if (c) controls.current.set(slug, c);
                else controls.current.delete(slug);
              }}
            />
          ))}
          {visible.length === 0 && (
            <p className="text-sm text-muted-foreground">
              No {statusFilter} drafts.
            </p>
          )}
        </div>
      )}
    </div>
  );
}

function DraftCard({
  draft,
  highlighted,
  onSaved,
  onDelete,
  register,
}: {
  draft: Draft;
  highlighted: boolean;
  onSaved: () => void;
  onDelete: () => void;
  register: (slug: string, c: DraftCardControls | null) => void;
}) {
  const [d, setD] = useState<Draft>(draft);
  const [dirty, setDirty] = useState(false);
  const dirtyRef = useRef(false);
  const [saving, setSaving] = useState(false);
  const [regenerating, setRegenerating] = useState(false);
  const [previewHtml, setPreviewHtml] = useState<string | null>(null);
  const [previewOpen, setPreviewOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  // Sync from the server copy only when there are no unsaved local edits —
  // a background refresh must never wipe in-progress editing.
  useEffect(() => {
    if (!dirtyRef.current) setD(draft);
  }, [draft]);

  const patch = (p: Partial<Draft>) => {
    setD((cur) => ({ ...cur, ...p }));
    dirtyRef.current = true;
    setDirty(true);
  };

  async function save() {
    setSaving(true);
    try {
      await api.saveDraft(d);
      dirtyRef.current = false;
      setDirty(false);
      toast.success("Draft saved.");
      onSaved();
    } catch (e) {
      toast.error(String((e as Error).message ?? e));
    } finally {
      setSaving(false);
    }
  }

  // Status changes persist immediately — no separate Save click needed. The
  // whole current draft is saved, so pending edits ride along.
  async function setStatus(status: DraftStatus) {
    if (status === d.status) return;
    const prev = d.status;
    const updated = { ...d, status };
    setD(updated);
    try {
      await api.saveDraft(updated);
      dirtyRef.current = false;
      setDirty(false);
      onSaved();
    } catch (e) {
      toast.error(String((e as Error).message ?? e));
      setD((cur) => ({ ...cur, status: prev }));
    }
  }

  // Re-register every render so the page's keyboard shortcuts always call
  // closures over the latest local state.
  useEffect(() => {
    register(draft.slug, {
      setStatus,
      save,
      scrollTo: () =>
        rootRef.current?.scrollIntoView({ behavior: "smooth", block: "center" }),
    });
    return () => register(draft.slug, null);
  });

  async function preview() {
    setPreviewOpen(true);
    setPreviewHtml(null);
    try {
      const html = await previewEmail({
        name: d.name,
        subject: d.subject,
        body: d.body,
      });
      setPreviewHtml(html);
    } catch {
      setPreviewHtml("<p style='padding:24px;font-family:sans-serif'>Preview failed.</p>");
    }
  }

  async function regenerate() {
    setRegenerating(true);
    try {
      // draft-one matches the contact by email/name and overwrites this draft.
      const res = await api.draftOne({ email: d.to || undefined, name: d.name || undefined });
      if (res.already_running) {
        toast.info("A draft job is already running — this one wasn't started.");
      } else {
        toast.success("Regenerating this email — it'll refresh here when done.");
      }
    } catch (e) {
      toast.error(String((e as Error).message ?? e));
    } finally {
      setRegenerating(false);
    }
  }

  const dimmed = d.status === "skip" || d.status === "sent";

  return (
    <Card
      ref={rootRef}
      className={cn(
        "relative overflow-hidden p-5 transition-opacity",
        dimmed && "opacity-65 focus-within:opacity-100 hover:opacity-100",
        dirty && "border-warn/50",
        highlighted && "ring-2 ring-brand",
      )}
    >
      <span
        className={cn("absolute inset-y-4 left-0 w-1 rounded-r", RAIL[d.status])}
      />
      <div className="space-y-4 pl-2">
        <div className="flex flex-wrap items-center gap-3">
          <Input
            value={d.name}
            onChange={(e) => patch({ name: e.target.value })}
            placeholder="Name"
            aria-label="Name"
            className="h-9 max-w-52 font-medium"
          />
          <Input
            value={d.to}
            onChange={(e) => patch({ to: e.target.value })}
            placeholder="email"
            aria-label="Email address"
            className="h-9 flex-1"
          />
          <Select
            value={d.status}
            onValueChange={(v) => setStatus(v as DraftStatus)}
          >
            <SelectTrigger className="h-9 w-32 font-medium">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {STATUSES.map((s) => (
                <SelectItem key={s} value={s}>
                  {s}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <Input
          value={d.subject}
          onChange={(e) => patch({ subject: e.target.value })}
          placeholder="Subject"
          aria-label="Subject"
          className="font-medium"
        />

        <Textarea
          value={d.body}
          onChange={(e) => patch({ body: e.target.value })}
          rows={10}
          aria-label="Email body"
          className="font-mono text-[13px] leading-relaxed"
        />

        <div className="flex flex-wrap items-center gap-2">
          <Button onClick={save} disabled={saving}>
            {saving ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <Save className="size-4" />
            )}
            Save
          </Button>
          <Button variant="outline" onClick={preview}>
            <Eye className="size-4" />
            Preview
          </Button>
          <Button
            variant="outline"
            onClick={regenerate}
            disabled={regenerating}
            title="Re-generate this email from scratch"
          >
            {regenerating ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <RefreshCw className="size-4" />
            )}
            Regenerate
          </Button>
          <Button
            variant="ghost"
            className="text-muted-foreground hover:text-destructive"
            onClick={onDelete}
          >
            <Trash2 className="size-4" />
            Delete
          </Button>
          {dirty && (
            <span className="ml-auto text-[13px] font-medium text-warn">
              unsaved changes
            </span>
          )}
        </div>
      </div>

      <Dialog open={previewOpen} onOpenChange={setPreviewOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle className="font-normal">
              <span className="text-muted-foreground">Subject:</span>{" "}
              {d.subject || "(no subject)"}
            </DialogTitle>
          </DialogHeader>
          <div className="overflow-hidden rounded-md border bg-white">
            {previewHtml === null ? (
              <div className="flex h-64 items-center justify-center text-muted-foreground">
                <Loader2 className="size-5 animate-spin" />
              </div>
            ) : (
              <iframe
                title="Email preview"
                srcDoc={previewHtml}
                className="h-[60dvh] max-h-[26rem] w-full"
              />
            )}
          </div>
        </DialogContent>
      </Dialog>
    </Card>
  );
}
