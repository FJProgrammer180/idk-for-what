"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import {
  Activity,
  Atom,
  LayoutDashboard,
  Beaker,
  Bot,
  ChartLine,
  FileText,
  LogOut,
  Menu,
  Moon,
  Radio,
  Settings,
  Sun,
  Table2,
  X,
} from "lucide-react";
import { useSession } from "@/lib/session";
import { useTheme } from "./theme";
import { Badge, Button, Spinner } from "./ui";
import { cn } from "@/lib/cn";

interface NavItem {
  href: string;
  label: string;
  icon: ReactNode;
  hint: string;
}

const NAV: NavItem[] = [
  { href: "/", label: "Dashboard", icon: <LayoutDashboard size={16} />, hint: "Account activity at a glance" },
  { href: "/lab", label: "Compute", icon: <Atom size={16} />, hint: "Run a method or a simulation without saving it" },
  { href: "/experiments", label: "Experiments", icon: <Beaker size={16} />, hint: "Stored specs, runs and verification" },
  { href: "/agent", label: "Agent", icon: <Bot size={16} />, hint: "Natural-language planning over the tool registry" },
  { href: "/datasets", label: "Datasets", icon: <Table2 size={16} />, hint: "Upload, fit and export measurements" },
  { href: "/reports", label: "Reports", icon: <FileText size={16} />, hint: "Markdown and PDF write-ups from stored runs" },
  { href: "/library", label: "Method library", icon: <ChartLine size={16} />, hint: "Documented preconditions, orders, assumptions" },
  { href: "/settings", label: "Settings", icon: <Settings size={16} />, hint: "Profile, compute limits, retention" },
];

/**
 * The frame every page shares: the session gate, the nav, and the provider banner.
 *
 * Two honesty rules are enforced here rather than in each page: a mock planner is labelled as
 * what it is on every screen that shows its output, and while the session is still being
 * resolved the page renders a status instead of guessing (showing "0 runs" for one frame would
 * read as "this account has nothing").
 */
export function AppShell({ children }: { children: ReactNode }) {
  const { user, loading, capabilities, provider, error, signOut } = useSession();
  const { theme, toggle } = useTheme();
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const [signingOut, setSigningOut] = useState(false);
  const [signOutError, setSignOutError] = useState<string | null>(null);

  useEffect(() => {
    setOpen(false);
  }, [pathname]);

  const authed = Boolean(user);

  async function onSignOut() {
    setSigningOut(true);
    setSignOutError(null);
    try {
      await signOut();
      window.location.href = "/login";
    } catch (cause) {
      setSignOutError(cause instanceof Error ? cause.message : String(cause));
      setSigningOut(false);
    }
  }

  const sidebar = (
    <div className="flex h-full flex-col gap-4 p-4">
      <Link href="/" className="flex items-center gap-2.5" aria-label="PHYSICS LAB AI home">
        <span className="grid h-8 w-8 place-items-center rounded-lg bg-accent text-white dark:text-[#0b0e16]">
          <Radio size={16} />
        </span>
        <span className="leading-tight">
          <span className="block text-[13.5px] font-semibold tracking-tight">PHYSICS LAB AI</span>
          <span className="block text-[11px] text-muted">computational workspace</span>
        </span>
      </Link>

      <nav aria-label="Workspace" className="flex-1 space-y-0.5">
        {NAV.map((item) => {
          const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              title={item.hint}
              aria-current={active ? "page" : undefined}
              className={cn(
                "flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-[13.5px] transition-colors",
                active ? "bg-accent-soft font-medium text-[color:var(--tone-accent-ink)]" : "text-muted hover:bg-surface-2 hover:text-ink",
                !authed && item.href !== "/login" ? "pointer-events-none opacity-45" : "",
              )}
            >
              <span className={cn(active ? "text-accent" : "opacity-80")}>{item.icon}</span>
              {item.label}
            </Link>
          );
        })}
      </nav>

      {provider ? (
        <div className="rounded-xl border border-border bg-surface-2 p-2.5 text-[11.5px] leading-relaxed">
          <p className="flex items-center justify-between gap-2 font-medium">
            <span className="flex items-center gap-1.5">
              <Bot size={13} /> {provider.provider}
            </span>
            {provider.is_mock ? <Badge tone="warning">mock</Badge> : <Badge tone="success">live</Badge>}
          </p>
          <p className="mt-1 text-muted">
            {provider.is_mock
              ? provider.note || "Deterministic rule planner: no model calls. It selects registered methods and refuses what it cannot compute."
              : provider.model
                ? `Live provider · ${provider.model}`
                : "Live provider configured server-side."}
          {provider.research_integration_configured ? null : (
            <p className="mt-1 text-muted">No retrieval integration: literature search answers "not performed" instead of inventing citations.</p>
          )}
          </p>
        </div>
      ) : null}

      {user ? (
        <div className="space-y-2 border-t border-border pt-3 text-[12px]">
          <p className="truncate" title={user.email}>
            <span className="font-medium">{user.display_name || user.email}</span>
            {user.role === "admin" ? <span className="ml-1.5 text-muted">(admin)</span> : null}
          </p>
          <div className="flex items-center gap-1.5">
            <Button size="sm" intent="ghost" onClick={onSignOut} disabled={signingOut} title="End this session">
              {signingOut ? <Spinner /> : <LogOut size={14} />} Sign out
            </Button>
          </div>
          {signOutError ? <p className="text-rose-500">{signOutError}</p> : null}
        </div>
      ) : null}
    </div>
  );

  return (
    <div className="min-h-screen lg:grid lg:grid-cols-[248px_minmax(0,1fr)]">
      <aside className="no-print sticky top-0 hidden h-screen border-r border-border bg-surface lg:block">{sidebar}</aside>

      <div className="flex min-h-screen flex-col">
        <header className="no-print flex items-center gap-2 border-b border-border bg-surface px-4 py-2.5 lg:hidden">
          <Button size="icon" intent="ghost" aria-label={open ? "Close navigation" : "Open navigation"} onClick={() => setOpen((value) => !value)}>
            {open ? <X size={18} /> : <Menu size={18} />}
          </Button>
          <span className="text-[13px] font-semibold">PHYSICS LAB AI</span>
          <span className="ml-auto flex items-center gap-1">
            <Button size="icon" intent="ghost" aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`} onClick={toggle}>
              {theme === "dark" ? <Sun size={16} /> : <Moon size={16} />}
            </Button>
          </span>
        </header>
        {open ? (
          <div className="no-print border-b border-border bg-surface lg:hidden" role="dialog" aria-label="Navigation">
            {sidebar}
          </div>
        ) : null}

        <div className="hidden items-center justify-end gap-2 border-b border-border bg-surface px-5 py-2 lg:flex">
          {error ? <span className="text-[12px] text-amber-600">session check failed: {error}</span> : null}
          <Button size="sm" intent="ghost" aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`} onClick={toggle}>
            {theme === "dark" ? <Sun size={15} /> : <Moon size={15} />}
            <span className="sr-only">Toggle theme</span>
          </Button>
        </div>

        <main className="min-w-0 flex-1 px-4 py-5 sm:px-6 sm:py-6">
          {loading ? (
            <div className="flex items-center gap-2 text-[13px] text-muted">
              <Spinner /> checking your session…
            </div>
          ) : authed || pathname.startsWith("/login") ? (
            children
          ) : (
            <div className="mx-auto mt-10 max-w-lg space-y-3 text-center">
              <Activity className="mx-auto text-accent" size={26} />
              <h1 className="text-lg font-semibold">Sign in to open your workspace</h1>
              <p className="text-[13px] text-muted">
                Runs, datasets and reports are per-account, so there is nothing to show until the
                session cookie is set.
              </p>
              <Link href="/login" className="inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-accent px-4 text-sm font-medium text-white hover:opacity-90 dark:text-[#0b0e16]">
                Go to sign in
              </Link>
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
