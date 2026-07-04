import { render } from "@react-email/render";
import { OutreachEmail } from "../../../../emails/outreach";

// Renders one draft with React Email so the compose page can show the exact
// HTML that will be sent (not a website-only approximation).
export async function POST(req: Request) {
  const { name, subject, body } = await req.json();
  const html = await render(
    OutreachEmail({ name, subject, body: body ?? "" }),
    { pretty: false },
  );
  return Response.json({ html });
}
