import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { LeaDetailPanel } from "@/components/LeaDetailPanel";
import { LeaTable } from "@/components/LeaTable";
import type { SortDir, SortKey } from "@/lib/leaFilters";
import type { SeverityLevel } from "@/lib/severity";

const SEVERITY_VALUES: readonly SeverityLevel[] = [
  "critical",
  "warning",
  "stale",
  "healthy",
];
const SORT_KEYS: readonly SortKey[] = [
  "name",
  "severity",
  "partner",
  "student_count",
  "enrollment_count",
  "latest_sync_at",
  "cursor_lag_days",
];
const DEFAULT_SORT: SortKey = "name";
const DEFAULT_DIR: SortDir = "asc";
const SEARCH_DEBOUNCE_MS = 200;

export function LeasPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const selectedLea = searchParams.get("lea");
  const setSelectedLea = useCallback(
    (id: string | null) => {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (id == null) next.delete("lea");
          else next.set("lea", id);
          return next;
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );

  const urlQuery = searchParams.get("q") ?? "";
  const [searchInput, setSearchInput] = useState(urlQuery);

  useEffect(() => {
    if (searchInput === urlQuery) return;
    const handle = window.setTimeout(() => {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (searchInput.trim() === "") next.delete("q");
          else next.set("q", searchInput);
          return next;
        },
        { replace: true },
      );
    }, SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(handle);
  }, [searchInput, urlQuery, setSearchParams]);

  const severityLevels = useMemo<SeverityLevel[]>(() => {
    const raw = searchParams.getAll("severity");
    return raw.filter((v): v is SeverityLevel =>
      SEVERITY_VALUES.includes(v as SeverityLevel),
    );
  }, [searchParams]);

  const sort: SortKey = (() => {
    const raw = searchParams.get("sort");
    return SORT_KEYS.includes(raw as SortKey)
      ? (raw as SortKey)
      : DEFAULT_SORT;
  })();
  // The dir param accepts only "asc" / "desc" explicitly; anything
  // else (including the unparameterized default) falls back to
  // DEFAULT_DIR so /leas with no query string lands on the
  // documented default ("name asc"). The earlier shape defaulted to
  // "desc" here, which silently overrode DEFAULT_DIR.
  const dirParam = searchParams.get("dir");
  const dir: SortDir =
    dirParam === "asc" || dirParam === "desc" ? dirParam : DEFAULT_DIR;

  const toggleSeverity = useCallback(
    (level: SeverityLevel) => {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          const current = prev.getAll("severity");
          next.delete("severity");
          if (current.includes(level)) {
            for (const v of current)
              if (v !== level) next.append("severity", v);
          } else {
            for (const v of current) next.append("severity", v);
            next.append("severity", level);
          }
          return next;
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );

  const onSortChange = useCallback(
    (nextSort: SortKey, nextDir: SortDir) => {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (nextSort === DEFAULT_SORT && nextDir === DEFAULT_DIR) {
            next.delete("sort");
            next.delete("dir");
          } else {
            next.set("sort", nextSort);
            next.set("dir", nextDir);
          }
          return next;
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );

  return (
    <>
      <div className="page-head">
        <div>
          <h1>LEAs</h1>
        </div>
        <div className="actions" />
      </div>

      <LeaTable
        selectedLea={selectedLea}
        onSelect={(lea) =>
          setSelectedLea(lea === selectedLea ? null : lea)
        }
        searchQuery={searchInput}
        onSearchChange={setSearchInput}
        severityLevels={severityLevels}
        onSeverityToggle={toggleSeverity}
        sort={sort}
        dir={dir}
        onSortChange={onSortChange}
      />
      <LeaDetailPanel
        leaId={selectedLea}
        onClose={() => setSelectedLea(null)}
      />
    </>
  );
}
