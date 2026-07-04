"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { ArrowRight, Loader2, TriangleAlert } from "lucide-react";
import { api, type SetupState } from "@/lib/api";
import { ResumeUpload } from "@/components/resume-upload";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
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
import { Skeleton } from "@/components/ui/skeleton";

const EMPTY: SetupState = {
  fields: { about: "", experience: "", interests: "", writing_sample: "" },
  name: "",
  phone: "",
  gmail_address: "",
  gmail_app_password: "",
  schools: "",
  resume_ok: false,
  resume_name: "",
  verify_persons: false,
  filter_by_research: false,
  web_research: true,
  quality_review: true,
  api_key_ok: false,
};

function Toggle({
  checked,
  onChange,
  title,
  hint,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  title: string;
  hint: string;
}) {
  return (
    <div className="flex items-start gap-3 py-1">
      <Switch checked={checked} onCheckedChange={onChange} className="mt-0.5" />
      <div className="space-y-1">
        <p className="text-sm font-medium leading-none">{title}</p>
        <p className="text-[13px] leading-snug text-muted-foreground">{hint}</p>
      </div>
    </div>
  );
}

export default function SetupPage() {
  const router = useRouter();
  const [s, setS] = useState<SetupState | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api.getState().then(setS).catch((e) => toast.error(String(e.message ?? e)));
  }, []);

  const st = s ?? EMPTY;
  const set = (patch: Partial<SetupState>) => setS({ ...st, ...patch });
  const setField = (k: keyof SetupState["fields"], v: string) =>
    setS({ ...st, fields: { ...st.fields, [k]: v } });

  async function save() {
    setSaving(true);
    try {
      await api.saveSettings({
        ...st.fields,
        name: st.name,
        phone: st.phone,
        gmail_address: st.gmail_address,
        gmail_app_password: st.gmail_app_password,
        web_research: st.web_research,
        quality_review: st.quality_review,
      });
      toast.success("Settings saved.");
    } catch (e) {
      toast.error(String((e as Error).message ?? e));
    } finally {
      setSaving(false);
    }
  }

  async function reset(kind: "contacts" | "drafts") {
    try {
      await (kind === "contacts" ? api.resetContacts() : api.resetDrafts());
      toast.success(kind === "contacts" ? "Scraped contacts cleared." : "All drafts cleared.");
    } catch (e) {
      toast.error(String((e as Error).message ?? e));
    }
  }

  if (!s) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-10 w-64" />
        <Skeleton className="h-64 w-full" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <header className="space-y-2">
        <h1 className="text-3xl">Set up your outreach</h1>
        <p className="max-w-2xl text-[15px] text-muted-foreground">
          Fill this in once. The more specific and honest you are, the better your
          emails — recipients will ask about anything you mention, so don&rsquo;t
          invent things.
        </p>
      </header>

      {!st.api_key_ok && (
        <div className="flex items-start gap-3 rounded-lg border border-warn/40 bg-warn-soft px-4 py-3 text-sm text-warn">
          <TriangleAlert className="mt-0.5 size-4 shrink-0" />
          <p>
            No Anthropic API key is configured. Whoever shared this tool needs to
            set <code className="font-mono text-xs">ANTHROPIC_API_KEY</code> in{" "}
            <code className="font-mono text-xs">.env</code> before scraping or
            drafting will work.
          </p>
        </div>
      )}

      {/* About you */}
      <Card>
        <CardHeader>
          <p className="eyebrow">About you</p>
          <CardTitle className="sr-only">About you</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Your full name">
              <Input
                value={st.name}
                placeholder="Jane Doe"
                onChange={(e) => set({ name: e.target.value })}
              />
            </Field>
            <Field label="Phone (shown in your sign-off)">
              <Input
                value={st.phone}
                placeholder="(555) 123-4567"
                onChange={(e) => set({ phone: e.target.value })}
              />
            </Field>
          </div>
          <Field label="Who you are & what you're looking for">
            <Textarea
              rows={3}
              value={st.fields.about}
              placeholder="e.g. 3rd-year CS undergrad at X University, looking for a Summer 2026 research position in machine learning."
              onChange={(e) => setField("about", e.target.value)}
            />
          </Field>
          <Field label="Research experience & relevant skills">
            <Textarea
              rows={5}
              value={st.fields.experience}
              placeholder="Projects (what you did, the method, the result, your role), coursework, languages, tools. Concrete beats vague."
              onChange={(e) => setField("experience", e.target.value)}
            />
          </Field>
          <Field label="Research interests — the kind of lab you want, and why">
            <Textarea
              rows={4}
              value={st.fields.interests}
              placeholder="The themes you genuinely care about. Used to connect your interests to each researcher's specific work."
              onChange={(e) => setField("interests", e.target.value)}
            />
          </Field>
          <Field label="A sample of your own writing (so emails sound like you)">
            <Textarea
              rows={4}
              value={st.fields.writing_sample}
              placeholder="Paste 1–3 short paragraphs you've written (an email, cover letter, or statement)."
              onChange={(e) => setField("writing_sample", e.target.value)}
            />
          </Field>
        </CardContent>
      </Card>

      {/* Email account */}
      <Card>
        <CardHeader>
          <p className="eyebrow">Your email — messages send from here</p>
          <CardTitle className="sr-only">Your email</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Gmail address">
              <Input
                type="email"
                value={st.gmail_address}
                placeholder="you@gmail.com"
                onChange={(e) => set({ gmail_address: e.target.value })}
              />
            </Field>
            <Field label="Gmail App Password">
              <Input
                type="password"
                value={st.gmail_app_password}
                placeholder="16-character app password"
                onChange={(e) => set({ gmail_app_password: e.target.value })}
              />
            </Field>
          </div>
          <p className="text-[13px] leading-snug text-muted-foreground">
            Not your normal password — a Gmail <b>App Password</b>. Turn on
            2-Step Verification, then create one at{" "}
            <a
              className="text-brand underline underline-offset-2"
              href="https://myaccount.google.com/apppasswords"
              target="_blank"
              rel="noopener"
            >
              myaccount.google.com/apppasswords
            </a>
            . It stays on this computer only.
          </p>
        </CardContent>
      </Card>

      {/* Resume */}
      <Card>
        <CardHeader>
          <p className="eyebrow">Resume</p>
          <CardTitle className="sr-only">Resume</CardTitle>
        </CardHeader>
        <CardContent>
          <ResumeUpload initialName={st.resume_name} initialOk={st.resume_ok} />
        </CardContent>
      </Card>

      {/* Email quality */}
      <Card>
        <CardHeader>
          <p className="eyebrow">Email quality</p>
          <CardTitle className="sr-only">Email quality</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <Toggle
            checked={st.web_research}
            onChange={(v) => set({ web_research: v })}
            title="Look up each recipient's recent papers (web search)"
            hint="Strongly recommended for reply rate — opens with a specific recent paper instead of a generic line. Slower, more API calls."
          />
          <Toggle
            checked={st.quality_review}
            onChange={(v) => set({ quality_review: v })}
            title="Quality-review each draft (second AI pass)"
            hint="A second AI call checks each email against the style guide and fixes issues. One extra call per draft; much more consistent."
          />
        </CardContent>
      </Card>

      <div className="flex items-center gap-3">
        <Button onClick={save} disabled={saving} className="min-w-32">
          {saving && <Loader2 className="size-4 animate-spin" />}
          Save settings
        </Button>
      </div>

      {/* Next steps */}
      <Card className="border-brand/30">
        <CardHeader>
          <p className="eyebrow">Next</p>
          <CardTitle className="sr-only">Next</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-wrap items-center gap-3">
          <Button onClick={() => router.push("/find")}>
            <ArrowRight className="size-4" />
            Find contacts
          </Button>
          <span className="text-[13px] text-muted-foreground">
            Search academia or industry for people to reach out to. Save your
            settings first.
          </span>
        </CardContent>
      </Card>

      {/* Danger zone */}
      <Card className="border-destructive/30 bg-destructive/[0.03]">
        <CardHeader>
          <p className="eyebrow text-destructive">Reset data</p>
          <CardDescription>
            Clear scraped data to start fresh. Your settings, Gmail, and resume are
            kept.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-3">
          <ConfirmReset
            label="Clear scraped contacts"
            body="Delete all scraped contacts? Your drafts are kept."
            onConfirm={() => reset("contacts")}
          />
          <ConfirmReset
            label="Clear all drafts"
            body="Delete ALL drafts? This cannot be undone."
            onConfirm={() => reset("drafts")}
          />
        </CardContent>
      </Card>
    </div>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-2">
      <Label className="text-[13px] font-medium">{label}</Label>
      {children}
    </div>
  );
}

function ConfirmReset({
  label,
  body,
  onConfirm,
}: {
  label: string;
  body: string;
  onConfirm: () => void;
}) {
  return (
    <Dialog>
      <DialogTrigger
        render={
          <Button
            variant="outline"
            className="border-destructive/40 text-destructive hover:bg-destructive/10 hover:text-destructive"
          />
        }
      >
        {label}
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{label}</DialogTitle>
          <DialogDescription>{body}</DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <DialogClose render={<Button variant="outline" />}>Cancel</DialogClose>
          <DialogClose
            render={<Button variant="destructive" onClick={onConfirm} />}
          >
            {label}
          </DialogClose>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
