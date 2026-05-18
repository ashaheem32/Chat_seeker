import Link from "next/link";

/**
 * App Router 404 page.
 *
 * Defining this file explicitly avoids the auto-generated Pages-Router
 * shim that Next.js otherwise builds for `/404`/`/500`, which trips a
 * "<Html> should not be imported outside of pages/_document" error
 * during static export — a known Next 14 bug when an app uses both
 * `output: "standalone"` and `optimizePackageImports`.
 */
export default function NotFound() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-4 bg-background px-6 text-center text-foreground">
      <p className="text-[10px] uppercase tracking-[0.22em] text-muted-foreground">
        404
      </p>
      <h1 className="font-display text-3xl font-semibold tracking-tight">
        We couldn’t find that page
      </h1>
      <p className="max-w-md text-sm text-muted-foreground">
        The link may be stale or the chat may have been deleted.
      </p>
      <Link
        href="/"
        className="mt-2 inline-flex items-center gap-1 rounded-full border border-border bg-card px-4 py-1.5 text-sm transition hover:border-primary/40 hover:text-primary"
      >
        Back to ChatLens
      </Link>
    </main>
  );
}
