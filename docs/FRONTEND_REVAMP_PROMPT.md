# Frontend Revamp Brief — `revora` (award-level) — for a coding agent

**You are** a senior product designer + frontend engineer with Awwwards-level taste. Reference bar: linear.app, vercel.com, resend.com, raycast.com. You implement as well as you design.

**Mission** — Completely revamp the frontend of **revora**, a hackathon project for *Graph Hacks: Context for AI Agents* (WeMakeDevs × FalkorDB; deadline Oct 18, 23:59 IST). The interface must look and feel like a funded product, not a hackathon dashboard. A judge must understand in ~10 seconds: **an agent failed → the failure was measured → memory recorded it → the next attempt was blocked** — and must immediately want to click "live workspace".

Win conditions the design must serve:
- The FalkorDB graph must be visibly doing real work (causal chains, not decoration).
- Honesty is a weapon: every claim on screen stays calibrated ("recorded evidence", "controlled local fault injection", "simulated").
- It will be judged partly through screen recordings at 1360×1000. Design for camera: large numerals, readable state changes, no pure black (#000 banding), no motion too subtle to survive video compression.

Work decisively. Keep the build green. Do not gold-plate beyond this brief. Do not touch anything outside `frontend/`.

---

## 1) Read first (recon, before writing any code)

- `frontend/app/page.js` — command chrome: nav, view switch (`Verified replay` default / `Live workspace`), connection flow, footer
- `frontend/app/EvidenceReplay.js` — the replay flagship page (fetch `/evidence.json`)
- `frontend/app/ReleaseOps.js` — live incident control (real HTTP + FalkorDB)
- `frontend/app/Runtime.js` — learning proof, ablations, held-out evaluation, saved runs
- `frontend/app/GraphDashboard.js` — FalkorDB graph (`/api/graph/state`, 7 s poll) + belief sidebar
- `frontend/app/Laboratory.js` — replay lab (observations, belief cards)
- `frontend/app/GroqAgent.js` — SSE reasoning stream + live execution feed
- `frontend/app/layout.js`, all `frontend/app/*.css`, `frontend/public/evidence.json`
- `frontend/tests/browser.cjs` — **the UI contract**. Do not modify this file.

---

## 2) Non-negotiable contract

### A. Brand casing — absolute rule
`revora` is always lowercase. No capital R anywhere: UI copy, metadata, aria-labels, empty states, alt text. Fix these known instances:

1. `layout.js` — title → `revora — evidence that changes the next decision`; description → lowercase sentence
2. `page.js` — `aria-label="Revora home"` → `"revora home"`
3. `page.js` — footer → `revora · ai remembers. revora learns.`
4. `EvidenceReplay.js` — "Revora turns observed outcomes…" → `revora turns observed outcomes…`
5. `Runtime.js` — "Revora upgrades a customer registry…" → lowercase
6. `ReleaseOps.js` — "…let Revora diagnose…" and "Revora measures the candidate…" → lowercase (2 spots)
7. `GraphDashboard.js` — empty-state mark `R` → `r`

Then run `grep -rin "revora" frontend/` and prove zero uppercase instances remain. The nav wordmark stays: lowercase `r` glyph in a squircle + the word `revora`.

**Voice:** adopt a lowercase editorial tone across all display copy you are free to rewrite — headings, eyebrows, buttons, labels, nav, footer, status lines, table headers. (e.g. `experience infrastructure for agents`, `a live incident. a measured recovery.`, `run learning agent` — except where protected below.) Body paragraphs may keep normal sentence case for readability. Never apply casing transforms to protected strings.

### B. Protected strings — must remain byte-identical
`frontend/tests/browser.cjs` asserts these. Keep the literal text (and the element roles: button/label/heading/cell as noted). Restyle them however you like — size, color, layout — but not a character of the copy:

- button `Live workspace` (exact), button `Connect` (exact)
- label `Workspace API key (if required)`, label `Service` (exact), label `Scenario` (exact)
- region accessible name `A migration agent that learns without losing invoices.` (this is Runtime's `h2` — keep the heading text)
- button `Run learning agent` (exact), button `Inspect observation 1` (exact), button `Run held-out evaluation`, button `Load saved history`
- text `2 tool attempt(s) · 0 prior observations retrieved` (exact full string), text `1 tool attempt(s) · 2 prior observations retrieved`
- text `Observation 1: Failure retained` (exact)
- cell `duplicates-25` (exact) and cell `canary_regression` (exact) — must render as `<td>`/cells inside the evaluation table; the table must keep exactly 12 body rows when shown (`tbody tr` count = 12)
- text `4/4` (exact)
- heading `Measured comparison`, heading `Saved runs` (must render as heading elements), heading `Groq reasoning agent`
- text `Decision revised by this evidence` (exact)
- text `The migration that locks the database · retries reopen the same saved experiment` (exact)
- text `Explore the optional Groq agent, belief graph, and replay laboratory` (exact — the `<summary>` that toggles supporting flows)
- button `Replay experiment`, and the observation buttons must keep uppercase literal matching the regex `/OBSERVATION 3/` (keep `OBSERVATION 3` casing on those)
- view switch buttons keep `Verified replay` / `Live workspace`
- 390 px viewport: zero horizontal overflow; console: zero page errors

### C. Behavior freeze (presentational revamp only)
- Same endpoints, same fetch/SSE logic, same state flow, same conditional rendering. Do not touch `request()`, `connect()`, polling intervals, headers, or any API call.
- All replay content still comes from `/evidence.json` at runtime — never hardcode any displayed value.
- Live panels render only real API results — never simulate, never invent metrics. Keep every honesty/disclaimer label.
- Keep `role="status"` on the connection message, `aria-live` regions, `role="img"` on the graph SVG, keyboard handlers on interactive SVG nodes, and all disabled/busy states.
- Entrance animations must finish within ~700 ms and must never block clicks or move bounding boxes indefinitely (Playwright actionability). No element under test may be mid-animation when clicked.

### D. Stack rules
- Next 15 App Router + React 19 + plain CSS with custom properties. No Tailwind, no CSS-in-JS, no UI kits, no icon packages. Icons: inline SVG, 1.5 px stroke, `currentColor`.
- Allowed new dependencies, nothing else: `motion` (v12+, import from `motion/react`) for orchestration/springs; `geist` npm package for fonts (`geist/font/sans`, `geist/font/mono`; fallback: Geist via `next/font/google`).
- Keep files in `frontend/app/`; you may add files (e.g. `tokens.css`, small primitives, `icon.svg`); do not rename existing files.
- `npm run build` must pass. Prefer CSS transitions for hovers/ambient; use `motion` for orchestrated sequences. Honor `prefers-reduced-motion` (with `motion` use `MotionConfig reducedMotion="user"`).

---

## 3) Art direction — "the memory observatory"

**Concept.** Instrument-grade telemetry for agent memory. The page should feel like precision equipment: near-black canvas, hairline structure, calm surfaces, and data as the only decoration. Mood: clinical, exact, quietly luminous. Explicitly avoid: neon cyberpunk, glassmorphism, giant gradients, bento-grid-everything, emoji icons, floating orbs.

**Signature motif — "the thread".** One continuous line language everywhere a causal chain appears: route lanes, the handoff chain, graph edges, the section spine. A hairline with flowing packets that connects *outcome → evidence → belief → next decision*. This is the identity of the design and the spine of the animations.

**Color tokens** (define in `:root`):
- `--bg-0:#070a0f` (page) · `--bg-1:#0b1017` (surface) · `--bg-2:#10161f` (raised) · `--bg-3:#161e2a` (hover)
- `--line-0:rgba(148,178,209,.08)` · `--line-1:rgba(148,178,209,.16)` (1 px hairlines)
- `--text-0:#eef3f9` · `--text-1:#a7b6c9` · `--text-2:#6b7d93`
- `--mint:#54e0b3` (memory/brand) · `--mint-glow:rgba(84,224,179,.14)` · `--fault:#ff756d` · `--warn:#ffc555`
- Domain data colors — keep exactly (they are meaningful): Experience `#ff9378`, Evidence `#82a9ff`, Belief `#54e0b3`, contradiction `#ff756d`, belief update `#c29aff`

**Typography.** Geist Sans for UI; Geist Mono for all data, IDs, codes, HTTP statuses, counters, timestamps. `font-variant-numeric: tabular-nums` on every number. Scale: display-xl `clamp(44px, 6.5vw, 80px)` / line-height 1.02 / tracking −0.045em / weight 500; display-l `clamp(32px, 4vw, 48px)`; h3 20 px; body 15 px/1.65; small 13 px; micro/eyebrow 11 px tracking +0.12em lowercase. Big numerals are a signature element: HTTP status codes and headline counters render large in mono weight 500.

**Space & shape.** 4 px spacing scale; section rhythm ≥ 96 px desktop / 64 px mobile; content max-width 1200 px. Radii 8/12/16 px + pills. Shadows only on floating layers (sticky bar, inspector, palette). Texture: static SVG grain overlay at ~3% opacity (fixed, pointer-events none); faint dot grid inside the graph stage; one localized radial mint glow behind the hero (no page-wide gradients). Style `::selection` mint; custom slim scrollbars; focus-visible ring = 2 px mint at 60% with 2 px offset, everywhere, always.

---

## 4) Motion system

**Tokens.** Easing `cubic-bezier(.16,1,.3,1)` (expo-out) for entrances; spring-equivalent overshoot `cubic-bezier(.34,1.3,.64,1)` or `motion` springs (stiffness ~260, damping ~24) for state changes; durations 120/200/320/560 ms; ambient loops 3–8 s. Rules: only transform/opacity/filter animate; entrances ≤ 600 ms; nothing blocks interaction; one orchestrated boot, then restraint; no scroll-jacking; no infinite loops except slow ambient and live-status pulses.

**Signature animations (implement all that apply):**
1. **Boot reveal** — nav → hero → first section, 40 ms stagger, translateY 10 px + fade. Wordmark `r` SVG stroke draws in (~500 ms), settles. Once per session.
2. **Status morph** — the `200 → 503 → 200` numerals and every changing metric animate as a digit roll/count with a brief tone-color glow pulse (200–600 ms). This is the page's most important animation: failure must be *felt*.
3. **Stage filmstrip** — the four recorded stages become a numbered filmstrip; selection crossfades content (8 px slide), morphs the big status numeral, and pulses the tone color once.
4. **Route lanes** (ReleaseOps, live view) — the stable/candidate routes as two SVG lanes with flowing dash packets, driven by *real* API state: on failure, the candidate lane turns fault-red and packets halt (one 4 px shake); on recovery, packets resume in mint; on memory-block, the candidate lane dims, a "0 probes" counter lands, and a mint "memory" shield pulse runs the lane. Use real `canary_samples` statuses/latencies to draw the probe ticks — never fake data.
5. **Handoff chain** — analyst → reviewer → planner nodes and edges draw in sequentially; a packet travels the chain; the reviewer verdict lands like a stamp (scale 1.4 → 1 with slight rotation); hovering any node highlights the full chain and dims the rest.
6. **Graph reveal** — on data load: nodes fade/scale in (96%→100%), edges draw via `stroke-dashoffset` 500–700 ms staggered ~30 ms; newest belief node gets a slow idle pulse; hover = neighborhood highlight (others dim to 35%); CONTRADICTS edges flash red once. On the 7 s poll, animate only *deltas* — never re-mount the whole graph. Empty state: slow rotating orbit ring with lowercase `r`.
7. **Stream lines** — new SSE/trace lines enter with 8 px slide + fade (120 ms); the active tool run gets a soft pulse; expansion of tool detail animates height (keep it cheap — `motion` layout or CSS grid-rows trick).
8. **Ablation growth** — comparison rows/stat bars stage in (opacity + 4 px rise, ~40 ms stagger); numeric deltas pop; the memory column can carry a small mint "−2 attempts" style chip when the data supports it.
9. **View switch** — the segmented indicator slides between `Verified replay` / `Live workspace` (spring 320 ms); content crossfades 150 ms. Use the View Transitions API when available; `motion` fallback.
10. **Connect flow** — connecting spinner in the button; success morphs the status line into a persistent pill with a mint pulse; failure = a single 4 px shake and the calm inline message (keep `role="status"`).
11. **Scroll spine** (replay page) — a 1 px left rail with ticks `01–04`; scroll progress fills the line (transform scaleY, rAF); ticks activate when their section is in view. Hidden < 1100 px.
12. **Microinteractions** — button press scale 0.98 (120 ms); interactive cards lift 1 px + border brightens + faint mint edge glow; links shift underline offset; inputs get mint focus ring + label color shift.

---

## 5) Screen-by-screen direction

**Command bar** (`page.js`) — sticky 56–64 px, `backdrop-filter: blur(12px)`, hairline bottom border. Left: `r` mark + `revora`. Center: track chip `graph hacks / memory & coordination`. Right: segmented view switch + connection status dot (mint pulse when connected). With `motion`, animate the shared indicator.

**Footer** — lowercase, two muted lines. Keep the semantic content (recorded evidence / one workspace hardening limits).

**Replay page** (`EvidenceReplay`) — the editorial flagship.
- Hero: kicker `experience infrastructure for agents`, display headline (the existing "an agent fails once. the next one inherits the lesson." is strong — keep or refine in the same voice), sub-paragraph, primary link `explore the evidence ↘` + quiet `open live workspace ↗`.
- Hero right: the **verification instrument card** — `200 → 503 → 200` as large mono numerals with tone colors, `verified local run` tag with pulsing dot, `download acceptance record ↗`.
- Slim recorded-evidence band (keep its meaning; lowercase styling).
- Sections `01 / observe → act → remember`, `02 / shared memory across agents`, `03 / persistence changes a live decision`, `04 / remove memory. compare the decision.` — anchored to the scroll spine (`#incident-proof` anchor must keep working).
- Stage filmstrip → content: big recorded HTTP status numeral, tone glow, explanation, incident id line; keep the "`0 candidate probes · prior incident …`" callout styled as a mint annotation.
- Handoff chain with sequential draw-in + a node inspector restyled as a floating code panel (keep the JSON content; keep close behavior).
- Tables: hairline separators, mono tabular numerals, 44 px rows, sticky uppercase-free lowercase headers, row hover highlight. `honest-result` notes become margin annotations (small mint hairline on the left, micro label `measured, not generalized`) — **keep the exact disclaimer words**.
- `details` JSON inspectors: keep, restyle as quiet dim code panels.
- Reproduce card at the end: keep `docker compose up -d --build` snippet and links.

**Live workspace** — connection panel as a command form; when connected, add a sticky sub-nav (release ops / runtime / graph / lab / groq) with smooth anchor scrolling. Keep the exact protected names above.
- `ReleaseOps` → the operations deck: three numbered actions (inject / diagnose & hand off / retry), live status strip (route · version · incident id · probes), the route-lane visual, the 12 probe ticks from real samples, the handoff result block, and `Persistent incident trail` (keep `BASED_ON` phrasing).
- `Runtime` → attempt ledger: two-run comparison with big numerals, evidence SVG restyled (keep `aria-label` and keyboard handlers, `Inspect observation 1` label), `Measured comparison` panel, `Saved runs` list.
- `GraphDashboard` → graph stage as the section hero (draw-in, hover highlight, delta animations); belief summary card; `what the agent learned` feed with outcome dots; keep poll behavior.
- `Laboratory` → console style; observation timeline buttons keep `OBSERVATION {n}` casing; belief cards with `meter`; keep `Replay experiment` flow and `Scenario` select options.
- `GroqAgent` → live execution feed as a stream console; tool-call cards expand; `RUNNING` pulse; keep heading text.

**Brand extras** — add `frontend/app/icon.svg` (mint lowercase `r` on dark, App Router picks it up automatically). Metadata: title `revora — evidence that changes the next decision`, lowercase description, `theme-color #070a0f`, `viewport` defaults.

---

## 6) Acceptance checklist (self-verify all)

- [ ] `grep -rin "revora" frontend/` shows zero capital-R instances; all rewriteable chrome copy is lowercase voice
- [ ] Every protected string from §2B is byte-identical with the same element role; `browser.cjs` would pass end-to-end (if Playwright available in the environment, run it; otherwise verify by inspection and a manual pass)
- [ ] `npm run build` passes; zero console errors in both views
- [ ] 390 px: no horizontal overflow, layout composes (sticky bar collapses gracefully)
- [ ] `prefers-reduced-motion: reduce` replaces all entrances with instant/fade
- [ ] All animations use transform/opacity/filter only; no layout thrash; graph polls animate deltas only
- [ ] Every number uses tabular numerals; every state change has a visible color + numeral cue that survives video compression
- [ ] Keyboard: every control reachable; focus ring visible; inspector closes via Esc and returns focus
- [ ] Contrast: body text ≥ 4.5:1 on its surface; mint reserved for meaning, not decoration
- [ ] Judge read in ≤ 10 s on the landing view: failure → measured → remembered → blocked, plus a visible path to try it live
- [ ] Screenshots at 1360×1000 and 390×844 look composed (they become README/demo assets)

---

## 7) Execution order + final report

1. Recon (read every file in §1), inventory of caps-R spots and protected strings
2. Foundation: tokens, fonts, metadata, `icon.svg`, command bar, footer — build check
3. Replay page revamp (hero, instrument card, spine, filmstrip, chain, tables) — build check
4. Live panels (ReleaseOps, Runtime, GraphDashboard, Laboratory, GroqAgent) — build check
5. Motion pass (install `motion`, implement animations 1–12, reduced-motion fallback)
6. QA pass (checklist §6), fix regressions, screenshots

**Final report format:** files changed · dependencies added · animation inventory (one line each) · build output · any protected string you could not keep (should be none — if there is one, stop and reconsider instead of changing `browser.cjs`) · screenshot paths · anything left undone.

---

## 8) Forbidden

No capital `Revora`. No fake/simulated data in live panels. No changing protected strings or `browser.cjs`. No Tailwind/CSS-in-JS/UI kits/icon packs. No heavy 3D (no three.js). No scroll-jacking. No sound. No autoplay video. No removing honesty labels. No touching `backend/`, API contracts, or endpoints. No lorem ipsum. No default browser focus outlines left unstyled. No pure #000 background. No infinite decorative loops that burn CPU. No hardcoded evidence values on the replay page.

**Optional stretch (only after §6 fully passes):** a `⌘K` command palette (linear-style) to jump between `verified replay` / `live workspace` and the sections, wire to existing state only. Nice-to-have, never at the cost of the checklist.
