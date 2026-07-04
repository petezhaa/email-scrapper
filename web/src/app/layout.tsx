import type { Metadata } from "next";
import { Inter, Fraunces, JetBrains_Mono } from "next/font/google";
import "./globals.css";
import { Toaster } from "@/components/ui/sonner";
import { SiteHeader } from "@/components/site-header";
import { JobBar } from "@/components/job-bar";
import { PageTransition } from "@/components/page-transition";
import { ThemeProvider } from "@/components/theme-provider";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
  display: "swap",
});
const fraunces = Fraunces({
  subsets: ["latin"],
  variable: "--font-fraunces",
  display: "swap",
  axes: ["opsz"],
});
const mono = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-mono-custom",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Research Outreach",
  description:
    "Find research contacts, draft honest outreach, send from your Gmail.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body
        className={`${inter.variable} ${fraunces.variable} ${mono.variable} antialiased min-h-dvh`}
      >
        <ThemeProvider attribute="class" defaultTheme="system" enableSystem>
          <SiteHeader />
          <main className="mx-auto w-full max-w-5xl px-6 pb-32 pt-10">
            <PageTransition>{children}</PageTransition>
          </main>
          <JobBar />
          <Toaster position="bottom-center" />
        </ThemeProvider>
      </body>
    </html>
  );
}
