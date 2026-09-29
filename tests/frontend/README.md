# Ask activity UI checks

Run the timeline reducer and scroll-controller regressions with `npm run test:agent-ui`. They use Node's
test runner and the existing TypeScript dependency.

For a browser smoke test, make `playwright` available in your Node environment
and start an isolated frontend:

```sh
SERVER_BASE_URL=http://127.0.0.1:18081 npm run dev -- --port 3107
node tests/frontend/ask-timeline.browser.cjs
```

For the focused scrolling regression, use the same frontend and run
`node tests/frontend/ask-scroll.browser.cjs` instead (the fixtures share port
18081, so do not run both simultaneously). This checks actual wheel scrolling,
an exact bottom position within 2px, stable page height when the jump control
changes, persistent Mermaid DOM, SSE growth, paused reading, viewport resizing,
and mobile-sized layouts. It reproduced the diagram remount on the old code;
this is a geometry/interaction test, not a zero-console-error assertion for the
unrelated existing CSS-as-script bundling issue.

Set `PLAYWRIGHT_CHANNEL=chrome` to use an installed Chrome instead of Playwright's
bundled Chromium. The test starts its own fixture backend on `127.0.0.1:18081`,
closes it afterward, and prints the temporary screenshot directory. Stop the
test frontend before running a production build (both use `.next`).

The browser test covers incremental SSE through the Next.js proxy, progress
retention, plans, tool errors, auto-scroll/pause, reconnect deduplication,
completed-session replay, and mobile overflow. It uses a dummy local identity
and synthetic events; it neither calls a model nor validates analysis of real
business repositories.

It also checks that partial indexes can be selected in Ask, that admin failure
details are visible, and that retry completion refreshes the coverage without a
page reload. If a local development bundler cannot load fonts, run the same check
against a production build instead:

```sh
SERVER_BASE_URL=http://127.0.0.1:18081 npm run build
npm exec next -- start --port 3107
```

The backend address is a build-time rewrite; rebuild without that override after
QA before using the local production bundle against your normal backend.
