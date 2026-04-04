export const CCHOST_API =
  process.env.NEXT_PUBLIC_CCHOST_API || "http://localhost:8420";

/** Returns true for file extensions that cannot be displayed as text. */
export function isBinaryFile(path: string): boolean {
  return /\.(pdf|xlsx|xls|zip|png|jpg|gif)$/i.test(path);
}
