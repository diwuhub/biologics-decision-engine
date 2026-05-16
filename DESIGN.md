# Design System — CMC Decision Workspace

Adapted from IBM Carbon (enterprise structure, token system, 8px grid) + Sentry (severity-based status colors) + Linear (clean light-mode surfaces). Tuned for biopharma regulatory professionals.

## 1. Visual Theme

Enterprise clinical precision. White canvas, near-black text, single accent blue. Color appears only for status signals (pass/caution/fail). Depth through subtle background layering, not shadows. Every spacing value on the 8px grid.

**Key rules:**
- White (`#ffffff`) canvas, Gray 10 (`#f4f4f4`) for card surfaces
- Near-black (`#161616`) for primary text — not pure black
- Single accent: Blue 60 (`#0f62fe`) for all interactive elements
- 0px border-radius on primary buttons — rectangular, no softening
- Flat cards — depth via background color, not box-shadow
- 8px spacing grid: `8, 16, 24, 32, 48` only

## 2. Color Palette

### Core

| Token | Hex | Role |
|-------|-----|------|
| `--text-primary` | `#161616` | Headings, primary text |
| `--text-secondary` | `#525252` | Secondary text, descriptions |
| `--text-placeholder` | `#6f6f6f` | Placeholder, disabled |
| `--text-caption` | `#8d8d8d` | Timestamps, metadata |
| `--bg-page` | `#ffffff` | Page background |
| `--bg-surface` | `#f4f4f4` | Cards, table headers, panels |
| `--bg-surface-hover` | `#e8e8e8` | Hover on surface elements |
| `--border-subtle` | `#e0e0e0` | Borders, dividers |
| `--border-strong` | `#c6c6c6` | Input borders, emphasized dividers |
| `--interactive` | `#0f62fe` | Buttons, links, focus rings |
| `--interactive-hover` | `#0043ce` | Hover on interactive elements |

### Status (from Sentry severity patterns)

| Token | Hex | Background | Border | Text | Use |
|-------|-----|-----------|--------|------|-----|
| `--status-pass` | `#24a148` | `#defbe6` | `#24a148` | `#0e6027` | CQA present, spec met, section found |
| `--status-caution` | `#f1c21b` | `#fdf6dd` | `#f1c21b` | `#735c0f` | Uncertain extraction, marginal spec |
| `--status-fail` | `#da1e28` | `#fff1f1` | `#da1e28` | `#750e13` | Data absent, spec violation, critical gap |
| `--status-info` | `#0f62fe` | `#edf5ff` | `#0f62fe` | `#002d9c` | Informational, disclaimer |

### Verdict card backgrounds (muted, not solid)

Verdict cards use muted tint backgrounds with colored left-border — never solid color blocks:

| Verdict | Background | Border-left | Text stays `#161616` |
|---------|-----------|-------------|---------------------|
| Adequate / Ready / Pass | `#defbe6` | `4px solid #24a148` | Dark text on light green |
| Needs Data / Caution | `#fdf6dd` | `4px solid #f1c21b` | Dark text on light yellow |
| Not Ready / Fail | `#fff1f1` | `4px solid #da1e28` | Dark text on light red |
| Assessed / Info | `#edf5ff` | `4px solid #0f62fe` | Dark text on light blue |
| Neutral | `#f4f4f4` | `4px solid #c6c6c6` | Dark text on gray |

## 3. Typography

### Font Stack
- **Primary**: `-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif`
- **Monospace**: `"SF Mono", Menlo, Consolas, "Liberation Mono", monospace`

No external font imports. System fonts only — reliable in all environments including Streamlit.

### Scale

| Role | Size | Weight | Line Height | Spacing | Use |
|------|------|--------|-------------|---------|-----|
| Page title | 24px | 600 | 1.25 | 0 | "CMC Decision Workspace" |
| Section heading | 18px | 600 | 1.33 | 0 | "Decision Panel", "Data" |
| Card title | 14px | 600 | 1.40 | 0 | Expander headers, field labels |
| Body | 14px | 400 | 1.50 | 0 | Standard text |
| Caption | 12px | 400 | 1.33 | 0.32px | Metadata, timestamps, disclaimer |
| Label (uppercase) | 11px | 600 | 1.25 | 0.08em | Verdict axis labels, table headers |
| Data value | 14px mono | 400 | 1.43 | 0 | CQA values, confidence scores |

### Rules
- **Three weights only**: 400 (body), 600 (emphasis/headings), no 700
- **Uppercase labels**: verdict axis labels, table column headers — always with letter-spacing 0.06-0.08em
- Weight 300 is not used — too light for a data-dense tool

## 4. Component Patterns

### Verdict Cards

Muted background, colored left border, dark text. Side-by-side flex layout:

```css
.verdict-card {
    background: var(--status-pass-bg);  /* #defbe6 */
    border-left: 4px solid var(--status-pass);  /* #24a148 */
    border-radius: 0;  /* IBM: rectangular */
    padding: 12px 16px;  /* 8px grid */
    color: #161616;  /* always dark text */
}
.verdict-card .label {
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: #525252;
}
.verdict-card .value {
    font-size: 16px;
    font-weight: 600;
    color: #161616;
    margin-top: 4px;
}
```

### Evidence Gap Items

Left-border cards grouped by status. Same pattern as verdict cards:

```
Present:   #defbe6 bg, #24a148 left-border
Uncertain: #fdf6dd bg, #f1c21b left-border
Absent:    #fff1f1 bg, #da1e28 left-border
```

### Data Tables

- Header row: `#f4f4f4` background, 11px uppercase labels, `#525252` text
- Body: 14px, `#161616` text
- Borders: `#e0e0e0`
- No row striping (Streamlit default)

### Metrics

Flat card on `#f4f4f4`:
- Label: 11px uppercase, `#525252`
- Value: 20px mono, `#161616`
- Border: `1px solid #e0e0e0`
- No shadow

### Buttons

- Primary: `#0f62fe` background, `#ffffff` text, 0px radius, 14px weight 600
- Secondary: `#ffffff` background, `1px solid #c6c6c6`, `#161616` text
- No gradients, no shadows, no hover animations

### Expanders

- Header: 14px weight 600, `#161616` text, `#f4f4f4` background
- No custom border — use Streamlit default

## 5. Spacing

8px base grid. Only these values:

| Token | Value | Use |
|-------|-------|-----|
| `--space-1` | 4px | Inner padding, tight gaps |
| `--space-2` | 8px | Standard inner spacing |
| `--space-3` | 16px | Card padding, element gaps |
| `--space-4` | 24px | Section spacing |
| `--space-5` | 32px | Major section breaks |
| `--space-6` | 48px | Page-level spacing |

## 6. Anti-Patterns

- **Never** use solid-color verdict blocks (green/red/amber full background with white text)
- **Never** import external fonts — system fonts only in Streamlit
- **Never** override Streamlit sidebar to dark mode — it fights the framework
- **Never** use gradient backgrounds on buttons
- **Never** use shadows for depth — use background-color layering
- **Never** use border-radius > 4px on functional elements (cards, buttons)
- **Never** hardcode colors inline — use the token names for maintainability

## 7. Streamlit-Specific Notes

- CSS injected via `st.markdown("<style>...</style>", unsafe_allow_html=True)` in `setup_page()`
- Keep CSS overrides minimal — only cards, metrics, status items, and verdict components
- Let Streamlit handle: sidebar, buttons, expanders, tabs, file uploader, data tables
- HTML components use inline styles referencing the hex values from Section 2
- Test in both Chrome and Safari — Streamlit renders differently
