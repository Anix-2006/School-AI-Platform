# UI shell

The UI is plain HTML, CSS, and JavaScript served by FastAPI. There is no build
step. Keep shared assets at `/styles.css`, `/api.js`, `/nav.js`, and
`/sidebar.js`.

## Entry routes

- `/` — role chooser
- Parent — `/flows/parent-chat`
- Teacher — `/flows/teacher-workspace`
- Management — `/flows/platform-overview`
- Other existing pages remain available from the sidebar under `/flows/...`.
- `/docs` — API documentation

The selected role and demo access token are kept in `sessionStorage`, so they
are cleared when the browser session ends.

## Shared files

- `ui/styles.css` — design tokens, components, states, responsive layout, and
  accessibility rules
- `ui/api.js` — the only network client; timeout, one 401 refresh/retry, and
  plain-language error handling
- `ui/nav.js` — route definitions and DOM-safe navigation rendering
- `ui/sidebar.js` — focus management, skip link, active page, offline status,
  and retry messaging
- `ui/index.html` — role entry page and original inline SVG brand mark

Use `textContent`, `createElement`, or escaped templates for API-provided data.
Do not insert untrusted values directly with `innerHTML`.

## Responsive shell

- Below `640px`: compact mobile content and stacked controls.
- From `640px`: tablet content gains wider padding and multi-column layouts
  where each flow supports them.
- From `1024px`: the fixed `17rem` sidebar and desktop content topbar are
  persistent. The hamburger, close button, and backdrop are hidden.
- From `1440px`: shell gutters widen while content stays centred at a maximum
  width of `80rem`.

The sidebar remains keyboard reachable on desktop. Mobile drawer focus
management and all layouts below `1024px` remain unchanged.

## Security and data limits

The token endpoint and workbook are demo-only. `sessionStorage` reduces token
persistence but does not protect against cross-site scripting; production
should use a real identity provider and hardened content-security policy.

School data comes from `data/school_data.xlsx`. It can be locked by Excel,
supports a single-worker demo setup, and is not a production database. The UI
must not imply that writes are durable, concurrent, or production-secure.
