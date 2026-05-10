import { NextResponse } from "next/server";

/**
 * Frontend health endpoint - separate from the backend's. Used by uptime
 * checks against the Next.js process itself.
 */
export async function GET() {
  return NextResponse.json({ status: "ok", service: "chatlens-frontend" });
}
