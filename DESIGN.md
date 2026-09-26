# Design System

Evo Code should feel like a precise developer instrument: dark, calm, information-dense, and honest about what is verified.

## Design principles

1. **Evidence first**: every claim shows where it comes from (`file:line`) and whether it is still true.
2. **Show the orchestration**: the Brain, agents and pipeline are visible while they work; motion communicates state, not decoration.
3. **Honest states**: no invented numbers; unknown values render as "—", local mode is labeled "DEMO / LOCAL ANALYSIS".
4. **Calm density**: cards, small uppercase labels, monospace for code and hashes, generous line height for prose.
5. **Keyboard and screen-reader friendly by default**.

## Color system

Defined as Tailwind v4 theme tokens in `frontend/src/index.css`.

| Token | Value | Use |
| --- | --- | --- |
| `ink-950` | `#05070b` | App background |
| `ink-900` / `ink-850` / `ink-800` | `#090d14` / `#0d121b` / `#111724` | Inputs, modal, node backgrounds |
| `brand-300…600` | violet `#c4b5fd → #7c3aed` | Brand, active state, running agents |
| `glow` | cyan `#22d3ee` | Gradient end, data packets |
| emerald | Tailwind emerald | VERIFIED, complete, recommendations |
| amber | Tailwind amber | STALE, failed agent, medium severity |
| rose / fuchsia | Tailwind rose / fuchsia | High / critical severity, rejected claims |
| sky | Tailwind sky | Low severity, annotations, function names |

Panels use a 7 % white border and a subtle vertical white gradient. A faint grid and two blurred glows form the backdrop.

## Typography

- UI: system sans stack (`Inter`, `-apple-system`, `Segoe UI` …); no web-font download, so it works offline.
- Code, hashes, paths, metrics: `JetBrains Mono` → `SF Mono`/`Menlo` fallback.
- Eyebrow labels: 11 px, 600 weight, 0.18 em tracking, uppercase, zinc-500.
- Hero: 72 px semibold with a violet → cyan text gradient.

## Spacing

4 px base (Tailwind scale). Panels: `p-5`, grids: `gap-3`/`gap-6`, page gutter `px-5`, max content width 1500 px (dashboard) / 1152 px (home).

## Cards

`.panel`: rounded-2xl, 1 px translucent border, soft inner highlight and drop shadow. Finding cards stack: badges row → title → `file:line` link → description → evidence line → locations → annotations → recommendation → hash footer.

## Buttons

| Variant | Use |
| --- | --- |
| Primary (violet → cyan gradient) | Analyze Codebase, Present, Re-run when stale |
| Secondary (glass) | Demo repo, Re-verify sources |
| Ghost | Icon-only navigation (prev/next) |
| Danger (amber) | "Apply edit" to the working copy |

All buttons show a spinner while loading and are disabled during the action.

## Badges

- **Severity**: uppercase text + colored dot (critical, high, medium, low, info). Color is never the only signal.
- **Verification**: icon + label: VERIFIED (badge-check), STALE (triangle), MISSING (file-x), INVALID LINE / UNSUPPORTED / UNSAFE PATH (circle-x), DERIVED (info).
- **Mode**: `DEMO / LOCAL ANALYSIS` (cyan) or `LLM · model` (violet).
- **Chips**: category, CWE, detectors (`rule · sql-injection`, `LLM`), memory type.

## Severity indicators

Sorted critical → info everywhere. Metric cards tint their icon: security (rose), duplicates (amber), verified (emerald, amber if anything is stale).

## Agent states

| State | Node | Line to Brain | Card |
| --- | --- | --- | --- |
| pending / queued | grey | dashed grey | description / "Waiting for the Brain…" |
| running | violet glow | animated violet dashes + moving packet | top shimmer bar, spinner |
| complete | emerald | solid emerald | real summary from the agent |
| failed | amber | dashed amber | "Failed. The rest of the analysis is still available." + error |
| skipped | dimmed | faint | routing reason |

The Brain itself shows `BRAIN IDLE / ACTIVE / COMPLETE / ERROR`, the live stage message, and a checklist (understanding → retrieving → routing → running → cross-validating → verifying → updating memory).

## Empty states

Icon tile + title + one sentence + optional action (e.g. "Walkthrough is generated after analysis", "No findings match these filters", "Repository not found → start a new analysis").

## Loading states

Skeleton blocks for cards and panels, inline spinners with a label ("Reading source from the working copy…"), live timeline with a pulsing "● live" marker, metrics show "—" plus "Analyzing…" until computed.

## Error states

Amber alert banner with title, message and optional action (Re-run). Used for backend offline, failed analysis, stale findings, failed searches and agent failures. API error messages are shown verbatim because they are written for users.

## Accessibility

- Semantic landmarks (`header`, `nav`, `main`), `aria-current` on tabs and pipeline steps, `role="dialog"` + `aria-modal` for the source viewer with focus moved to Close, Escape to close and focus restored.
- Live regions for the Brain message, timeline and edit results.
- Visible `:focus-visible` outline in brand violet; all interactive elements are buttons or links.
- `MotionConfig reducedMotion="user"` plus CSS `prefers-reduced-motion` disable spinning, dashes and packets.
- Contrast: body text zinc-200/300 on ink backgrounds; status always paired with text.
- Keyboard: walkthrough supports ← / →.

Full WCAG conformance still requires manual testing with assistive technologies and an expert accessibility review.

## Responsive behavior

- ≥ 1280 px: two-column dashboard (Brain + agent grid; architecture + cross-validation), two-column masonry findings.
- 768–1279 px: stacked sections, agent grid 2 columns, tabs scroll horizontally.
- < 768 px: single column; the source viewer becomes a bottom sheet with the verification panel below the code; pipeline and request flows wrap.
