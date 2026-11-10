import { useEffect, useState } from "react";

export type ThemeChoice = "system" | "light" | "dark";

/** localStorage key; index.html reads it too, so the saved theme applies before the first paint. */
export const THEME_KEY = "vs-theme";

/** In the order the theme menu lists them. */
export const THEME_CHOICES: ThemeChoice[] = ["system", "light", "dark"];

function readChoice(): ThemeChoice {
  try {
    const v = window.localStorage.getItem(THEME_KEY);
    return v === "light" || v === "dark" ? v : "system";
  } catch {
    return "system";
  }
}

/** "system" follows the OS setting (no data-theme attribute); "light" and "dark" pin the theme. */
export function useTheme(): [ThemeChoice, (t: ThemeChoice) => void] {
  const [choice, setChoice] = useState<ThemeChoice>(readChoice);

  useEffect(() => {
    const root = document.documentElement;
    if (choice === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", choice);
    try {
      if (choice === "system") window.localStorage.removeItem(THEME_KEY);
      else window.localStorage.setItem(THEME_KEY, choice);
    } catch {
      // storage blocked (private mode): the theme still applies for this visit
    }
  }, [choice]);

  return [choice, setChoice];
}

export const THEME_LABEL: Record<ThemeChoice, string> = { system: "System", light: "Light", dark: "Dark" };
