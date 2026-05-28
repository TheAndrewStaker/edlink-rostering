type BadgeTone = "ok" | "warn" | "bad" | "info" | "mute" | "stale" | "ghost";

interface DsBadgeProps {
  tone: BadgeTone;
  children: React.ReactNode;
  style?: React.CSSProperties;
  title?: string;
}

export function DsBadge({ tone, children, style, title }: DsBadgeProps) {
  return (
    <span className={`ds-badge ${tone}`} style={style} title={title}>
      <span className="dot" />
      {children}
    </span>
  );
}

export type { BadgeTone };
