"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import {
  ArrowRight,
  Building2,
  GraduationCap,
  Loader2,
  Search,
} from "lucide-react";
import { api, type ContactCategory } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Switch } from "@/components/ui/switch";
import { Separator } from "@/components/ui/separator";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Skeleton } from "@/components/ui/skeleton";

const MODES: Record<
  ContactCategory,
  {
    label: string;
    icon: typeof GraduationCap;
    placeholder: string;
    blurb: string;
    presets: string[];
  }
> = {
  research: {
    label: "Academia",
    icon: GraduationCap,
    placeholder: "e.g. structural biology, machine learning, organic chemistry",
    blurb:
      "Searches universities and department faculty directories, then scrapes individual professors.",
    presets: [
      "structural biology",
      "machine learning",
      "organic chemistry",
      "neuroscience",
      "genomics",
      "materials science",
    ],
  },
  industry: {
    label: "Industry",
    icon: Building2,
    placeholder: "e.g. biotech drug discovery, computational biology, protein engineering",
    blurb:
      "Finds current job openings in this field (title, company, posting link) and who to contact — added to Contacts.",
    presets: [
      "biotech drug discovery",
      "computational biology",
      "AI / ML research",
      "protein engineering",
      "gene therapy",
      "immunology",
    ],
  },
};

export default function FindPage() {
  const router = useRouter();
  const [tab, setTab] = useState<ContactCategory>("research");
  const [query, setQuery] = useState("");
  const [schools, setSchools] = useState("");
  const [filterByResearch, setFilterByResearch] = useState(false);
  const [verifyPersons, setVerifyPersons] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState<"discover" | "scrape" | null>(null);

  useEffect(() => {
    api
      .getState()
      .then((s) => {
        setSchools(s.schools);
        setFilterByResearch(s.filter_by_research);
        setVerifyPersons(s.verify_persons);
      })
      .finally(() => setLoaded(true));
  }, []);

  const mode = MODES[tab];
  const Icon = mode.icon;

  async function saveOpts() {
    await api.saveSettings({
      schools,
      filter_by_research: filterByResearch,
      verify_persons: verifyPersons,
    });
  }

  async function find() {
    if (!query.trim()) {
      toast.error("Type a field or keyword to search for.");
      return;
    }
    setBusy("discover");
    try {
      await saveOpts();
      await api.runDiscover(query.trim(), tab);
      router.push("/contacts");
    } catch (e) {
      toast.error(String((e as Error).message ?? e));
      setBusy(null);
    }
  }

  async function scrapeUrls() {
    if (!schools.trim()) {
      toast.error("Add at least one URL or organization name.");
      return;
    }
    setBusy("scrape");
    try {
      await saveOpts();
      await api.runScrape(tab);
      router.push("/contacts");
    } catch (e) {
      toast.error(String((e as Error).message ?? e));
      setBusy(null);
    }
  }

  return (
    <div className="space-y-6">
      <header className="space-y-2">
        <h1 className="text-3xl">Find contacts</h1>
        <p className="max-w-2xl text-[15px] text-muted-foreground">
          Search a field and the app finds organizations, locates their people
          pages, and scrapes individual contacts — tagged by where they work.
        </p>
      </header>

      <Tabs
        value={tab}
        onValueChange={(v) => setTab(v as ContactCategory)}
        className="gap-6"
      >
        <TabsList className="grid w-full max-w-sm grid-cols-2">
          {(Object.keys(MODES) as ContactCategory[]).map((k) => {
            const M = MODES[k].icon;
            return (
              <TabsTrigger key={k} value={k} className="gap-1.5">
                <M className="size-4" />
                {MODES[k].label}
              </TabsTrigger>
            );
          })}
        </TabsList>

        <Card>
          <CardHeader>
            <p className="eyebrow flex items-center gap-1.5">
              <Icon className="size-3.5" />
              {mode.label} search
            </p>
            <CardTitle className="sr-only">{mode.label} search</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex flex-col gap-3 sm:flex-row">
              <div className="relative flex-1">
                <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && find()}
                  placeholder={mode.placeholder}
                  className="pl-9"
                />
              </div>
              <Button onClick={find} disabled={!!busy} className="sm:w-44">
                {busy === "discover" ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : (
                  <ArrowRight className="size-4" />
                )}
                {tab === "industry" ? "Find jobs" : "Find contacts"}
              </Button>
            </div>

            <div className="flex flex-wrap gap-2">
              {mode.presets.map((p) => (
                <button
                  key={p}
                  type="button"
                  onClick={() => setQuery(p)}
                  className="rounded-full border border-border bg-secondary/50 px-3 py-1 text-xs text-muted-foreground transition-colors hover:border-brand/50 hover:text-foreground"
                >
                  {p}
                </button>
              ))}
            </div>
            <p className="text-[13px] text-muted-foreground">{mode.blurb}</p>
          </CardContent>
        </Card>
      </Tabs>

      {/* Specific URLs / orgs */}
      <Card>
        <CardHeader>
          <p className="eyebrow">Or add specific URLs / organizations</p>
          <CardTitle className="sr-only">Specific URLs</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {loaded ? (
            <Textarea
              rows={3}
              value={schools}
              onChange={(e) => setSchools(e.target.value)}
              className="font-mono text-[13px]"
              placeholder={"Genentech\nRelay Therapeutics\nhttps://www.somecompany.com/team"}
            />
          ) : (
            <Skeleton className="h-20 w-full" />
          )}
          <div className="flex flex-wrap items-center gap-3">
            <Button variant="outline" onClick={scrapeUrls} disabled={!!busy}>
              {busy === "scrape" ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <ArrowRight className="size-4" />
              )}
              Scrape these as {MODES[tab].label}
            </Button>
            <span className="text-[13px] text-muted-foreground">
              Company/university names are looked up; direct people-page URLs are
              scraped as-is.
            </span>
          </div>
        </CardContent>
      </Card>

      {/* Advanced */}
      <Card>
        <CardHeader>
          <p className="eyebrow">Search options</p>
          <CardTitle className="sr-only">Search options</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <Opt
            checked={filterByResearch}
            onChange={setFilterByResearch}
            title="AI-filter contacts by field match"
            hint="A fast AI call per person skips contacts whose work doesn't connect to your background. Slower, more relevant."
          />
          <Opt
            checked={verifyPersons}
            onChange={setVerifyPersons}
            title="AI-verify each person is an individual"
            hint="Skips lab or team pages that aren't a single person."
          />
        </CardContent>
      </Card>
    </div>
  );
}

function Opt({
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
    <div className="flex items-start gap-3">
      <Switch checked={checked} onCheckedChange={onChange} className="mt-0.5" />
      <div className="space-y-1">
        <p className="text-sm font-medium leading-none">{title}</p>
        <p className="text-[13px] leading-snug text-muted-foreground">{hint}</p>
      </div>
    </div>
  );
}
