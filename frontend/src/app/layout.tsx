import type { Metadata, Viewport } from "next";
import "./globals.css";
import { AppShell } from "@/components/app-shell";
import { SessionProvider } from "@/lib/session";
import { ThemeProvider } from "@/components/theme";

export const metadata: Metadata = {
  title: {
    default: "PHYSICS LAB AI",
    template: "%s · PHYSICS LAB AI",
  },
  description:
    "Computational physics workspace: an agentic planner that only ever calls registered numerical methods, cross-checks every run against an independent computation, and reports convergence honestly.",
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f6f7fb" },
    { media: "(prefers-color-scheme: dark)", color: "#0b0e16" },
  ],
};

/**
 * The whole client is one workspace: providers wrap a shell that gates on the session, so no
 * page has to remember whether it may call the API. Nothing here ships sample or placeholder
 * data - an unauthenticated visitor sees the login screen, not a mock dashboard.
 */
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" data-theme="light" suppressHydrationWarning>
      <body className="min-h-screen bg-canvas text-ink antialiased">
        <ThemeProvider>
          <SessionProvider>
            <AppShell>{children}</AppShell>
          </SessionProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
