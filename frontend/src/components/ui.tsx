import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/cn";

const button = cva(
  "relative inline-flex items-center justify-center gap-2 rounded-lg font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-55 select-none touch-manipulation",
  {
    variants: {
      intent: {
        primary: "bg-accent text-white hover:opacity-90 dark:text-[#0b0e16]",
        secondary: "bg-surface-2 text-ink border border-border hover:bg-surface-3",
        ghost: "text-muted hover:bg-surface-2 hover:text-ink",
        danger: "bg-rose-600 text-white hover:bg-rose-700",
        outline: "border border-accent/50 text-accent hover:bg-accent-soft",
      },
      size: {
        sm: "h-8 px-3 text-[13px]",
        md: "h-10 px-4 text-sm",
        lg: "h-11 px-5 text-[15px]",
        icon: "h-8 w-8",
      },
      full: { true: "w-full", false: "" },
    },
    defaultVariants: { intent: "secondary", size: "md", full: false },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof button> {}

export function Button({ className, intent, size, full, type = "button", ...props }: ButtonProps) {
  return (
    <button type={type} className={cn(button({ intent, size, full }), className)} {...props} />
  );
}

export function Card({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("panel p-4 sm:p-5", className)} {...props} />;
}

export function SectionTitle({
  title,
  hint,
  icon,
  actions,
  className,
}: {
  title: string;
  hint?: string;
  icon?: React.ReactNode;
  actions?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("mb-3 flex flex-wrap items-end justify-between gap-2", className)}>
      <div className="flex items-start gap-2.5">
        {icon ? <span className="mt-0.5 text-accent">{icon}</span> : null}
        <div>
          <h2 className="text-[15px] font-semibold leading-tight">{title}</h2>
          {hint ? <p className="mt-0.5 text-[12.5px] text-muted">{hint}</p> : null}
        </div>
      </div>
      {actions ? <div className="flex items-center gap-2">{actions}</div> : null}
    </div>
  );
}

const badge = cva("inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11.5px] font-medium whitespace-nowrap", {
  variants: {
    tone: {
      neutral: "border-border bg-surface-2 text-muted",
      info: "border-sky-500/30 bg-sky-500/10 text-sky-700 dark:text-sky-300",
      success: "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
      warning: "border-amber-500/40 bg-amber-500/10 text-amber-800 dark:text-amber-300",
      danger: "border-rose-500/40 bg-rose-500/10 text-rose-800 dark:text-rose-300",
      accent: "border-accent/35 bg-accent-soft text-[color:var(--tone-accent-ink)]",
    },
  },
  defaultVariants: { tone: "neutral" },
});

export function Badge({
  tone,
  className,
  dot,
  ...props
}: React.HTMLAttributes<HTMLSpanElement> & VariantProps<typeof badge> & { dot?: boolean }) {
  return (
    <span className={cn(badge({ tone }), className)} {...props}>
      {dot ? <span className="h-1.5 w-1.5 rounded-full bg-current opacity-80" aria-hidden /> : null}
      {props.children}
    </span>
  );
}

export function Spinner({ className, label = "working" }: { className?: string; label?: string }) {
  return (
    <span
      role="status"
      aria-label={label}
      className={cn(
        "inline-block h-3.5 w-3.5 animate-spin rounded-full border-[1.5px] border-current border-t-transparent align-[-2px]",
        className,
      )}
    />
  );
}

export function Alert({
  tone = "info",
  title,
  children,
  className,
}: {
  tone?: "info" | "warning" | "danger" | "success" | "neutral";
  title?: string;
  children: React.ReactNode;
  className?: string;
}) {
  const tones: Record<string, string> = {
    info: "border-sky-500/35 bg-sky-500/[0.07]",
    warning: "border-amber-500/45 bg-amber-500/[0.08]",
    danger: "border-rose-500/45 bg-rose-500/[0.08]",
    success: "border-emerald-500/40 bg-emerald-500/[0.07]",
    neutral: "border-border bg-surface-2",
  };
  return (
    <div className={cn("rounded-xl border px-3.5 py-3 text-[13.5px] leading-relaxed", tones[tone], className)}>
      {title ? <p className="mb-1 text-[11px] font-semibold uppercase tracking-wider">{title}</p> : null}
      <div className="space-y-1.5">{children}</div>
    </div>
  );
}

export function EmptyState({ title, hint, action }: { title: string; hint?: string; action?: React.ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed border-border px-6 py-10 text-center">
      <p className="text-sm font-medium">{title}</p>
      {hint ? <p className="max-w-md text-[12.5px] text-muted">{hint}</p> : null}
      {action ? <div className="mt-2">{action}</div> : null}
    </div>
  );
}

export function Field({
  label,
  hint,
  error,
  required,
  htmlFor,
  children,
  className,
}: {
  label: string;
  hint?: string;
  error?: string | null;
  required?: boolean;
  htmlFor?: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("space-y-1", className)}>
      <label htmlFor={htmlFor} className="flex items-baseline justify-between gap-2">
        <span className="text-[12.5px] font-medium">
          {label}
          {required ? <span className="ml-0.5 text-rose-500">*</span> : null}
        </span>
        {hint ? <span className="text-[11px] text-muted">{hint}</span> : null}
      </label>
      {children}
      {error ? (
        <p className="text-[11.5px] text-rose-600 dark:text-rose-400" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}

export function KeyValue({ rows, className }: { rows: [string, React.ReactNode][]; className?: string }) {
  return (
    <dl className={cn("grid grid-cols-[minmax(0,1fr)_minmax(0,1.6fr)] gap-x-4 gap-y-1.5 text-[13px]", className)}>
      {rows.map(([key, value]) => (
        <div key={key} className="contents">
          <dt className="text-muted">{key}</dt>
          <dd className="mono break-words">{value}</dd>
        </div>
      ))}
    </dl>
  );
}
