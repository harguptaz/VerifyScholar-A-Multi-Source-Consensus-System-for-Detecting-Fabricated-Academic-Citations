import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { THEME_CHOICES, THEME_LABEL, type ThemeChoice } from "../theme";

interface Props {
  theme: ThemeChoice;
  onChange: (t: ThemeChoice) => void;
}

/** Navbar dropdown for the colour theme: System (follows the OS setting), Light or Dark. */
export function ThemeMenu({ theme, onChange }: Props) {
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const items = useRef<(HTMLButtonElement | null)[]>([]);

  // on open, focus the current choice so the arrow keys start from it
  useEffect(() => {
    if (open) items.current[THEME_CHOICES.indexOf(theme)]?.focus();
  }, [open]); // only when it opens, not when the choice changes

  // a click anywhere outside closes the menu
  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      if (wrap.current && !wrap.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", onDown);
    return () => document.removeEventListener("pointerdown", onDown);
  }, [open]);

  const close = (refocus: boolean) => {
    setOpen(false);
    if (refocus) button.current?.focus();
  };

  const pick = (t: ThemeChoice) => {
    onChange(t);
    close(true);
  };

  const onMenuKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const list = items.current.filter(Boolean) as HTMLButtonElement[];
    const i = list.indexOf(document.activeElement as HTMLButtonElement);
    const go = (n: number) => { e.preventDefault(); list[(n + list.length) % list.length]?.focus(); };
    if (e.key === "ArrowDown") go(i + 1);
    else if (e.key === "ArrowUp") go(i - 1);
    else if (e.key === "Home") go(0);
    else if (e.key === "End") go(list.length - 1);
    else if (e.key === "Escape") {
      // handled here, so it doesn't also close the help panel or cancel a Paper Finder request
      e.preventDefault();
      e.stopPropagation();
      close(true);
    } else if (e.key === "Tab") close(false);
  };

  return (
    <div className="theme-menu" ref={wrap}>
      <button
        ref={button}
        className={open ? "help-btn theme-btn active" : "help-btn theme-btn"}
        onClick={() => setOpen((v) => !v)}
        onKeyDown={(e) => { if (e.key === "ArrowDown" && !open) { e.preventDefault(); setOpen(true); } }}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls="theme-menu-list"
        aria-label={`Theme: ${THEME_LABEL[theme]}`}
        title="Theme"
      >
        <ThemeIcon theme={theme} /> <span className="help-label" aria-hidden="true">{THEME_LABEL[theme]}</span>
        <svg className="caret" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M6 9l6 6 6-6" /></svg>
      </button>
      {open && (
        <div id="theme-menu-list" className="theme-menu-list" role="menu" aria-label="Theme" onKeyDown={onMenuKey}>
          {THEME_CHOICES.map((t, i) => (
            <button
              key={t}
              ref={(el) => { items.current[i] = el; }}
              type="button"
              role="menuitemradio"
              aria-checked={t === theme}
              tabIndex={-1}
              className={t === theme ? "theme-option selected" : "theme-option"}
              onClick={() => pick(t)}
            >
              <ThemeIcon theme={t} />
              <span className="theme-option-label">
                {THEME_LABEL[t]}
                {t === "system" && <span className="theme-option-hint">Follows your device</span>}
              </span>
              <svg className="theme-check" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M5 12l5 5 9-11" /></svg>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/** Monitor for System, sun for Light, moon for Dark. */
function ThemeIcon({ theme }: { theme: ThemeChoice }) {
  const common = { width: 18, height: 18, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 2, strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true };
  if (theme === "light") return <svg {...common}><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></svg>;
  if (theme === "dark") return <svg {...common}><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" /></svg>;
  return <svg {...common}><rect x="3" y="4" width="18" height="12" rx="2" /><path d="M8 20h8M12 16v4" /></svg>;
}
