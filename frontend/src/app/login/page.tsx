"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Atom, ShieldCheck } from "lucide-react";
import { useSession } from "@/lib/session";
import { ApiFailure } from "@/lib/api";
import { Alert, Button, Card, Field, Spinner } from "@/components/ui";

/**
 * Sign in / create account.
 *
 * The password rule shown here is the server's own (>= 10 characters, not a known-breach string);
 * it is stated up front because a client-side check that disagrees with the backend would only
 * make people pick worse passwords. Credentials go to `POST /auth/login` and nowhere else - this
 * app never stores a token in localStorage, the session is an HttpOnly cookie.
 */
export default function LoginPage() {
  const router = useRouter();
  const { user, signIn, signUp, loading } = useSession();
  const [mode, setMode] = useState<"signin" | "signup">("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});

  useEffect(() => {
    if (!loading && user) router.replace("/");
  }, [loading, user, router]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setFieldErrors({});
    try {
      if (mode === "signin") {
        await signIn(email.trim(), password);
      } else {
        await signUp(email.trim(), password, displayName.trim());
      }
      router.replace("/");
      router.refresh();
    } catch (cause) {
      if (cause instanceof ApiFailure) {
        setError(cause.message);
        // FastAPI's validation details name the field, so put each complaint where it belongs
        if (cause.code === "validation_error" && Array.isArray(cause.details)) {
          const next: Record<string, string> = {};
          for (const entry of cause.details as { loc?: unknown[]; msg?: string }[]) {
            const field = (entry.loc ?? []).filter((part) => part !== "body").join(".");
            if (field && entry.msg) next[field] = entry.msg;
          }
          setFieldErrors(next);
        }
      } else {
        setError(cause instanceof Error ? cause.message : String(cause));
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto flex min-h-screen w-full max-w-md flex-col justify-center gap-4 px-4 py-10">
      <div className="flex items-center gap-2.5">
        <span className="grid h-9 w-9 place-items-center rounded-xl bg-accent text-white dark:text-[#0b0e16]">
          <Atom size={18} />
        </span>
        <div>
          <p className="text-[15px] font-semibold leading-tight">PHYSICS LAB AI</p>
          <p className="text-[12px] text-muted">computational workspace with verified runs</p>
        </div>
      </div>

      <Card className="space-y-4">
        <div className="flex gap-1 rounded-xl bg-surface-2 p-1 text-[13px]">
          {(["signin", "signup"] as const).map((value) => (
            <button
              key={value}
              type="button"
              onClick={() => {
                setMode(value);
                setError(null);
              }}
              className={`flex-1 rounded-lg px-3 py-1.5 font-medium transition-colors ${
                mode === value ? "bg-surface text-ink shadow-sm" : "text-muted hover:text-ink"
              }`}
            >
              {value === "signin" ? "Sign in" : "Create account"}
            </button>
          ))}
        </div>

        <form onSubmit={submit} className="space-y-3">
          <Field label="Email" htmlFor="email" error={fieldErrors.email ?? null} required>
            <input
              id="email"
              className="field"
              type="email"
              autoComplete="username"
              required
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              placeholder="you@institution.edu"
            />
          </Field>

          {mode === "signup" ? (
            <Field label="Display name" htmlFor="display" hint="optional" error={fieldErrors.display_name ?? null}>
              <input
                id="display"
                className="field"
                value={displayName}
                onChange={(event) => setDisplayName(event.target.value)}
                placeholder="A. Researcher"
              />
            </Field>
          ) : null}

          <Field
            label="Password"
            htmlFor="password"
            hint={mode === "signup" ? "10+ characters" : undefined}
            error={fieldErrors.password ?? null}
            required
          >
            <input
              id="password"
              className="field"
              type="password"
              autoComplete={mode === "signin" ? "current-password" : "new-password"}
              required
              minLength={mode === "signup" ? 10 : 1}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
          </Field>

          {error ? <Alert tone="danger">{error}</Alert> : null}

          <Button type="submit" intent="primary" full size="lg" disabled={busy}>
            {busy ? <Spinner /> : <ShieldCheck size={16} />}
            {mode === "signin" ? "Sign in" : "Create account"}
          </Button>
        </form>

        <p className="text-[11.5px] leading-relaxed text-muted">
          Runs, datasets and reports are scoped to this account. The demo instance seeded by
          <span className="mono"> python -m app.scripts.seed_demo</span> has eight worked
          experiments you can inspect, each with its verification record.
        </p>
      </Card>
    </main>
  );
}
