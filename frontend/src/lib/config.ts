export const CCHOST_API =
  process.env.NEXT_PUBLIC_CCHOST_API ||
  (typeof window !== "undefined"
    ? `${window.location.protocol}//${window.location.hostname}:${window.location.protocol === "https:" ? "8443" : "8420"}`
    : "http://localhost:8420");

/** Returns true for file extensions that cannot be displayed as text. */
export function isBinaryFile(path: string): boolean {
  return /\.(pdf|xlsx|xls|zip|png|jpg|gif)$/i.test(path);
}

/** Extract filename from a path. */
export function getFileName(path: string): string {
  return path.split("/").pop() || path;
}

/** Group file paths by their directory. */
export function groupByDirectory(files: string[]): Map<string, string[]> {
  const groups = new Map<string, string[]>();
  for (const f of files) {
    const idx = f.lastIndexOf("/");
    const dir = idx >= 0 ? f.substring(0, idx) : "";
    if (!groups.has(dir)) groups.set(dir, []);
    groups.get(dir)!.push(f);
  }
  return groups;
}
