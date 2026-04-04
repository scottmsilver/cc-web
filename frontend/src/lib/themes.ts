export type Theme = {
  name: string;
  id: string;
  bg: string;
  surface: string;
  surfaceHover: string;
  border: string;
  text: string;
  textMuted: string;
  textFaint: string;
  accent: string;
  accentHover: string;
  userBubble: string;
  userBubbleBorder: string;
  assistantBubble: string;
  codeBg: string;
  codeText: string;
  termBg: string;
  termText: string;
};

export const themes: Theme[] = [
  {
    name: "Light",
    id: "light",
    bg: "#ffffff",
    surface: "#f9fafb",
    surfaceHover: "#f3f4f6",
    border: "#e5e7eb",
    text: "#111827",
    textMuted: "#6b7280",
    textFaint: "#9ca3af",
    accent: "#c46847",
    accentHover: "#a85537",
    userBubble: "#eff6ff",
    userBubbleBorder: "#dbeafe",
    assistantBubble: "#f3f4f6",
    codeBg: "#f9fafb",
    codeText: "#c2410c",
    termBg: "#111827",
    termText: "#4ade80",
  },
  {
    name: "Dark",
    id: "dark",
    bg: "#1a1a1a",
    surface: "#232323",
    surfaceHover: "#2d2d2d",
    border: "#3a3a3a",
    text: "#e8e4df",
    textMuted: "#8a8580",
    textFaint: "#6a6560",
    accent: "#d77757",
    accentHover: "#e8946e",
    userBubble: "#2a2520",
    userBubbleBorder: "#4a3f35",
    assistantBubble: "#2d2d2d",
    codeBg: "#1a1a1a",
    codeText: "#d77757",
    termBg: "#0d0d0d",
    termText: "#4ade80",
  },
  {
    name: "Warm",
    id: "warm",
    bg: "#faf8f5",
    surface: "#f5f0eb",
    surfaceHover: "#ede6de",
    border: "#ddd5ca",
    text: "#2c2520",
    textMuted: "#7a706a",
    textFaint: "#a09890",
    accent: "#c46847",
    accentHover: "#a85537",
    userBubble: "#fff7ed",
    userBubbleBorder: "#fed7aa",
    assistantBubble: "#f5f0eb",
    codeBg: "#f0ebe5",
    codeText: "#b45030",
    termBg: "#1c1816",
    termText: "#a3e635",
  },
  {
    name: "Nord",
    id: "nord",
    bg: "#2e3440",
    surface: "#3b4252",
    surfaceHover: "#434c5e",
    border: "#4c566a",
    text: "#eceff4",
    textMuted: "#d8dee9",
    textFaint: "#81a1c1",
    accent: "#88c0d0",
    accentHover: "#8fbcbb",
    userBubble: "#434c5e",
    userBubbleBorder: "#4c566a",
    assistantBubble: "#3b4252",
    codeBg: "#2e3440",
    codeText: "#a3be8c",
    termBg: "#242933",
    termText: "#a3be8c",
  },
];

export function getTheme(id: string): Theme {
  return themes.find((t) => t.id === id) || themes[0];
}

export function applyTheme(theme: Theme) {
  const root = document.documentElement;
  root.style.setProperty("--th-bg", theme.bg);
  root.style.setProperty("--th-surface", theme.surface);
  root.style.setProperty("--th-surface-hover", theme.surfaceHover);
  root.style.setProperty("--th-border", theme.border);
  root.style.setProperty("--th-text", theme.text);
  root.style.setProperty("--th-text-muted", theme.textMuted);
  root.style.setProperty("--th-text-faint", theme.textFaint);
  root.style.setProperty("--th-accent", theme.accent);
  root.style.setProperty("--th-accent-hover", theme.accentHover);
  root.style.setProperty("--th-user-bubble", theme.userBubble);
  root.style.setProperty("--th-user-bubble-border", theme.userBubbleBorder);
  root.style.setProperty("--th-assistant-bubble", theme.assistantBubble);
  root.style.setProperty("--th-code-bg", theme.codeBg);
  root.style.setProperty("--th-code-text", theme.codeText);
  root.style.setProperty("--th-term-bg", theme.termBg);
  root.style.setProperty("--th-term-text", theme.termText);

  // Save preference
  try { localStorage.setItem("cchost-theme", theme.id); } catch {}
}

export function loadSavedThemeId(): string {
  try { return localStorage.getItem("cchost-theme") || "light"; } catch { return "light"; }
}
