import {
  Body,
  Container,
  Head,
  Html,
  Preview,
  Section,
  Text,
} from "@react-email/components";

export type OutreachEmailProps = {
  /** Recipient's name, only used for the preview line. */
  name?: string;
  subject?: string;
  /** Plain-text body the user wrote/edited (paragraphs split on blank lines). */
  body: string;
};

// Kept intentionally plain: cold research outreach should read like a personal
// email, not a marketing template. React Email gives us email-safe, inlined
// HTML that renders consistently across Gmail/Outlook/Apple Mail.
export function OutreachEmail({ name, subject, body }: OutreachEmailProps) {
  const paragraphs = body
    .replace(/\r\n/g, "\n")
    .split(/\n{2,}/)
    .map((p) => p.trim())
    .filter(Boolean);

  return (
    <Html lang="en">
      <Head />
      <Preview>{subject || `Message for ${name ?? "you"}`}</Preview>
      <Body style={main}>
        <Container style={container}>
          <Section>
            {paragraphs.map((p, i) => (
              <Text key={i} style={text}>
                {p.split("\n").map((line, j, arr) => (
                  <span key={j}>
                    {line}
                    {j < arr.length - 1 ? <br /> : null}
                  </span>
                ))}
              </Text>
            ))}
          </Section>
        </Container>
      </Body>
    </Html>
  );
}

export default OutreachEmail;

const main: React.CSSProperties = {
  backgroundColor: "#ffffff",
  fontFamily:
    '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif',
};

const container: React.CSSProperties = {
  margin: "0 auto",
  padding: "24px 0",
  maxWidth: "600px",
};

const text: React.CSSProperties = {
  fontSize: "15px",
  lineHeight: "1.6",
  color: "#1a1a1a",
  margin: "0 0 16px",
};
