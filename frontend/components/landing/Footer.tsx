import Link from "next/link";
import { MessageSquareText } from "lucide-react";

export function Footer() {
  return (
    <footer className="border-t border-border/70 bg-card/40">
      <div className="mx-auto flex w-full max-w-6xl flex-col items-center justify-between gap-4 px-5 py-8 text-sm text-muted-foreground sm:flex-row sm:px-6">
        <Link href="/" className="flex items-center gap-2 text-foreground">
          <span className="flex h-7 w-7 items-center justify-center rounded-md bg-gradient-to-br from-primary to-accent text-primary-foreground">
            <MessageSquareText className="h-3.5 w-3.5" />
          </span>
          <span className="font-display font-semibold">ChatLens</span>
          <span className="ml-1 rounded-full border border-border px-2 py-0.5 text-[10px]">beta · 0.1</span>
        </Link>
        <nav className="flex items-center gap-5" aria-label="Footer">
          <a href="#features" className="transition hover:text-foreground">Features</a>
          <a href="#how" className="transition hover:text-foreground">How it works</a>
          <a href="#privacy" className="transition hover:text-foreground">Privacy</a>
          <Link href="/upload" className="transition hover:text-foreground">Open app</Link>
        </nav>
        <p className="text-xs">Built with Claude, pgvector and a lot of chat logs.</p>
      </div>
    </footer>
  );
}
