import { render } from "@react-email/render";
import { OutreachEmail } from "../../../../emails/outreach";

const PY_API = process.env.PY_API_BASE ?? "http://127.0.0.1:5000";

type Draft = {
  slug: string;
  to: string;
  name: string;
  subject: string;
  status: string;
  body: string;
};

// Send flow that honors the chosen stack: React Email builds the email HTML
// here (Node), then the local Python service does the actual Gmail/SMTP send
// with that HTML as the rich part. Returns a job id the JobBar polls.
export async function POST() {
  const draftsRes = await fetch(`${PY_API}/api/drafts`, { cache: "no-store" });
  if (!draftsRes.ok) {
    return Response.json({ error: "Could not load drafts." }, { status: 502 });
  }
  const { items } = (await draftsRes.json()) as { items: Draft[] };
  const approved = items.filter((d) => d.status === "approved");

  if (approved.length === 0) {
    return Response.json(
      { error: "No drafts are marked approved." },
      { status: 400 },
    );
  }

  const html_map: Record<string, string> = {};
  for (const d of approved) {
    html_map[d.slug] = await render(
      OutreachEmail({ name: d.name, subject: d.subject, body: d.body }),
      { pretty: false },
    );
  }

  const runRes = await fetch(`${PY_API}/api/run/send`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ html_map }),
  });
  const data = await runRes.json();
  if (!runRes.ok) {
    return Response.json(
      { error: data?.error || "Send failed to start." },
      { status: 502 },
    );
  }
  return Response.json({ job_id: data.job_id, count: approved.length });
}
