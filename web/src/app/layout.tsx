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
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50 focus:rounded-lg focus:bg-background focus:px-3 focus:py-2 focus:text-sm focus:font-medium focus:ring-1 focus:ring-foreground/10"
        >
          Skip to content
        </a>
        <ThemeProvider attribute="class" defaultTheme="system" enableSystem>
          <SiteHeader />
          <main id="main" className="mx-auto w-full max-w-5xl px-6 pb-32 pt-10">
            <PageTransition>{children}</PageTransition>
          </main>
          <JobBar />
          {/* Offset keeps bottom-center toasts clear of the job bar. */}
          <Toaster position="bottom-center" offset={96} mobileOffset={96} />
        </ThemeProvider>
      </body>
    </html>
  );
}
