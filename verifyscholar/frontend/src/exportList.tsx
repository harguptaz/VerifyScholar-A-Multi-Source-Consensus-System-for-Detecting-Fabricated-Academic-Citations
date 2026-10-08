import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { recordKey } from "./format";
import type { PaperRecord } from "./types";

/** The session export list (F36): papers the user picked while searching. Lives in this tab only. */
interface ExportListValue {
  items: PaperRecord[];
  add: (r: PaperRecord) => void;
  remove: (r: PaperRecord) => void;
  has: (r: PaperRecord) => boolean;
  clear: () => void;
}

const STORAGE_KEY = "verifyscholar.exportList";
const Ctx = createContext<ExportListValue | null>(null);

function load(): PaperRecord[] {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as PaperRecord[]) : [];
  } catch {
    return [];
  }
}

export function ExportListProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<PaperRecord[]>(load);

  useEffect(() => {
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(items));
    } catch {
      /* storage full or disabled: the list simply will not survive a refresh */
    }
  }, [items]);

  const add = useCallback((r: PaperRecord) => setItems((cur) => (cur.some((x) => recordKey(x) === recordKey(r)) ? cur : [...cur, r])), []);
  const remove = useCallback((r: PaperRecord) => setItems((cur) => cur.filter((x) => recordKey(x) !== recordKey(r))), []);
  const has = useCallback((r: PaperRecord) => items.some((x) => recordKey(x) === recordKey(r)), [items]);
  const clear = useCallback(() => setItems([]), []);

  const value = useMemo(() => ({ items, add, remove, has, clear }), [items, add, remove, has, clear]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useExportList(): ExportListValue {
  const v = useContext(Ctx);
  if (!v) throw new Error("useExportList must be used inside ExportListProvider");
  return v;
}
