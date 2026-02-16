import { NextRequest, NextResponse } from 'next/server';

/**
 * Middleware to support nested GitLab group paths in the URL.
 *
 * Next.js route `[owner]/[repo]` only matches 2 dynamic segments.
 * GitLab projects under nested groups have paths like `bas/rpc/aggregator`
 * (3+ segments).  This middleware rewrites such paths so the route can match:
 *
 *   /bas/rpc/aggregator  →  /bas%2Frpc/aggregator       (wiki page)
 *   /bas/rpc/aggregator/slides  →  /bas%2Frpc/aggregator/slides
 *
 * The first segment of the URL stays as-is (becomes `[owner]` for simple
 * repos).  All middle segments are folded into the first segment via `%2F`
 * encoding, and the last meaningful segment becomes `[repo]`.
 *
 * Known static routes (admin, auth, wiki, api, _next, health) are skipped.
 */

// Segments that are known static routes and should NOT be rewritten.
const STATIC_PREFIXES = new Set([
  'admin',
  'auth',
  'wiki',
  'api',
  '_next',
  'health',
  'favicon.ico',
]);

// Known sub-routes under [owner]/[repo]/
const REPO_SUB_ROUTES = new Set(['slides', 'workshop']);

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  // Split into non-empty segments
  const segments = pathname.split('/').filter(Boolean);

  // Only process paths with 3+ segments whose first segment is not a static route
  if (segments.length < 3 || STATIC_PREFIXES.has(segments[0])) {
    return NextResponse.next();
  }

  // Determine if the last segment is a known sub-route (slides, workshop)
  const lastSegment = segments[segments.length - 1];
  const hasSubRoute = REPO_SUB_ROUTES.has(lastSegment);

  let ownerSegments: string[];
  let repoSegment: string;
  let suffix = '';

  if (hasSubRoute) {
    // e.g. /bas/rpc/aggregator/slides → owner = bas/rpc, repo = aggregator, suffix = /slides
    // Only rewrite if there are 4+ segments (3 for group path + 1 for sub-route)
    if (segments.length < 4) {
      return NextResponse.next();
    }
    ownerSegments = segments.slice(0, -2); // all except repo and sub-route
    repoSegment = segments[segments.length - 2];
    suffix = `/${lastSegment}`;
  } else {
    // e.g. /bas/rpc/aggregator → owner = bas/rpc, repo = aggregator
    ownerSegments = segments.slice(0, -1); // all except repo
    repoSegment = segments[segments.length - 1];
  }

  // Encode the multi-segment owner by joining with %2F
  const encodedOwner = ownerSegments.map(encodeURIComponent).join('%2F');

  // Build the rewritten URL
  const url = request.nextUrl.clone();
  url.pathname = `/${encodedOwner}/${encodeURIComponent(repoSegment)}${suffix}`;

  return NextResponse.rewrite(url);
}

export const config = {
  // Only run on paths that could be dynamic wiki routes.
  // Exclude Next.js internal routes and common static file extensions.
  matcher: [
    '/((?!_next/static|_next/image|favicon\\.ico|.*\\.(?:js|css|map|png|jpg|jpeg|gif|svg|ico|woff|woff2|ttf|eot|json)$).*)',
  ],
};