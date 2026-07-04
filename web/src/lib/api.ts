// Thin client for the local Python pipeline API, proxied at /py/* (see
// next.config.ts). Everything is same-origin, so no CORS and no base URL.

export type ContactCategory = "research" | "industry";

export type Contact = {
  name: string;
  email: string;
  title: string;
  affiliation: string;
  research_interests: string;
  profile_url: string;
  source_url: string;
  category: ContactCategory;
};

export type DraftStatus = "pending" | "approved" | "skip" | "sent";

export type Draft = {
  slug: string;
  to: string;
  name: string;
  subject: string;
  status: DraftStatus;
  source_url: string;
  body: string;
};

export type SetupState = {
  fields: {
    about: string;
    experience: string;
    interests: string;
    writing_sample: string;
  };
  name: string;
  phone: string;
  gmail_address: string;
  gmail_app_password: string;
  schools: string;
  resume_ok: boolean;
  resume_name: string;
  verify_persons: boolean;
  filter_by_research: boolean;
  web_research: boolean;
  quality_review: boolean;
  api_key_ok: boolean;
};

export type Job = {
  id: string;
  kind: string;
  label: string;
  status: "running" | "done" | "error";
  last: string;
  steps: number;
  error: string | null;
};

export type JobsResponse = {
  jobs: Job[];
  draft_count: number;
  contact_count: number;
};

async function j<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/py/${path}`, {
    headers: { "Content-Type": "application/json" },
    cache: "no-store",
    ...init,
  });
  if (!res.ok) {
    let detail = "";
    try {
      detail = (await res.json())?.error ?? "";
    } catch {
      /* ignore */
    }
    throw new Error(detail || `Request failed (${res.status})`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  getState: () => j<SetupState>("state"),
  saveSettings: (body: Partial<SetupState> & Record<string, unknown>) =>
    j<{ ok: true }>("settings", { method: "POST", body: JSON.stringify(body) }),
  uploadResume: (file: File) => {
    const fd = new FormData();
    fd.append("resume", file);
    // Let the browser set the multipart boundary; don't send JSON headers.
    return fetch("/py/resume", { method: "POST", body: fd }).then((r) => {
      if (!r.ok) throw new Error("Resume upload failed");
      return r.json() as Promise<{ ok: true; resume_name: string }>;
    });
  },

  getContacts: () => j<{ rows: Contact[] }>("contacts"),
  setContactEmail: (profile_url: string, email: string) =>
    j("contacts/set-email", {
      method: "POST",
      body: JSON.stringify({ profile_url, email }),
    }),
  deleteContact: (id: { email?: string; profile_url?: string }) =>
    j("contacts/delete", { method: "POST", body: JSON.stringify(id) }),

  getDrafts: () => j<{ items: Draft[]; counts: Record<string, number> }>("drafts"),
  saveDraft: (d: Omit<Draft, "status"> & { status: string }) =>
    j<{ ok: true }>("drafts/save", { method: "POST", body: JSON.stringify(d) }),
  deleteDraft: (slug: string) =>
    j("drafts/delete", { method: "POST", body: JSON.stringify({ slug }) }),

  resetContacts: () => j("reset/contacts", { method: "POST" }),
  resetDrafts: () => j("reset/drafts", { method: "POST" }),

  runDiscover: (query: string, category: ContactCategory, findEmails = false) =>
    j<{ job_id: string }>("run/discover", {
      method: "POST",
      body: JSON.stringify({ query, category, find_emails: findEmails }),
    }),
  runScrape: (category: ContactCategory) =>
    j<{ job_id: string }>("run/scrape", {
      method: "POST",
      body: JSON.stringify({ category }),
    }),
  runDraft: () => j<{ job_id: string }>("run/draft", { method: "POST" }),
  // send is triggered through the Next route (renders React Email first)
  startSend: (html_map: Record<string, string>) =>
    j<{ job_id: string }>("run/send", {
      method: "POST",
      body: JSON.stringify({ html_map }),
    }),

  jobs: () => j<JobsResponse>("jobs"),
};

// Next-side routes (React Email rendering lives in Node, not Python)
export async function previewEmail(input: {
  name: string;
  subject: string;
  body: string;
}): Promise<string> {
  const res = await fetch("/api/preview", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!res.ok) throw new Error("Preview render failed");
  return (await res.json()).html as string;
}

export async function sendApproved(): Promise<{ job_id: string; count: number }> {
  const res = await fetch("/api/send", { method: "POST" });
  const data = await res.json();
  if (!res.ok) throw new Error(data?.error || "Send failed to start");
  return data;
}
