# Dashboard Chart Tooltip — Dark Mode Text Color Fix

## Problem

On the Dashboard page (`frontend/src/routes/dashboard.tsx`), the "Calls per day"
line chart uses a Recharts `<Tooltip>`. In **dark mode**, the tooltip text is nearly
invisible: the tooltip box keeps its default light/white background (which is fine),
but the label and value text render in a light color, so it's light-on-white.

### Root cause

The `<Tooltip>` `contentStyle` sets font, border, and radius but **does not set a
text `color`**. Recharts renders the tooltip into a portal/popup whose default
background is white, while the text inherits the app's foreground color. In dark mode
`--color-text` is `#e8e8e2` (near-white), so the text disappears against the white
tooltip background.

Relevant theme variables (`frontend/src/styles/global.css`):

- Light: `--color-text: #1a1a18`, `--color-surface: #ffffff`
- Dark:  `--color-text: #e8e8e2`, `--color-surface: #1c1c1a`

Also note: Recharts inline-styles the item/label text, so a plain CSS rule may be
overridden — the fix should be applied via the Tooltip props.

## Goal

Tooltip text must be readable in **both** light and dark mode, with the box
background remaining acceptable. The cleanest fix is to make the tooltip box use the
theme surface color and the text use the theme text color, so it's always consistent
with the rest of the UI.

## Files to change

### `frontend/src/routes/dashboard.tsx` (the `<Tooltip>` around line 71)

Update the `<Tooltip>` so background and text track the theme variables:

1. Add `backgroundColor: "var(--color-surface)"` and `color: "var(--color-text)"`
   to `contentStyle`. This makes the box use the dark surface in dark mode and the
   text the readable theme color in both modes.
2. Add `labelStyle={{ color: "var(--color-text)" }}` and
   `itemStyle={{ color: "var(--color-text)" }}`. Recharts applies inline styles to
   the label and each item separately; `contentStyle.color` alone does **not** always
   propagate to them, so these two props are required to actually recolor the text.

Resulting Tooltip (illustrative):

```tsx
<Tooltip
  contentStyle={{
    fontFamily: "var(--font-mono)",
    fontSize: 11,
    border: "1px solid var(--color-border-strong)",
    borderRadius: 0,
    backgroundColor: "var(--color-surface)",
    color: "var(--color-text)",
  }}
  labelStyle={{ color: "var(--color-text)" }}
  itemStyle={{ color: "var(--color-text)" }}
/>
```

## Alternative considered (not chosen)

Keep the white box and only darken text via a fixed dark color (e.g.
`color: "#1a1a18"`). This fixes dark mode but is brittle: it hardcodes a color and
would look wrong if the surface/background ever changes. Using theme variables
(surface + text together) is more robust and matches the rest of the dashboard.

## Verification

1. Run the frontend dev server (`make` target — check `make help`).
2. Open the Dashboard page.
3. Toggle dark mode (ThemeToggle / `data-theme="dark"`) and also test OS dark mode
   with no explicit choice.
4. Hover over the "Calls per day" line chart and confirm the tooltip label (date) and
   value (calls) text are clearly readable in both light and dark mode.
5. Run `make lint-frontend` to confirm no TS/lint regressions.

## Notes / scope

- Only the Dashboard chart tooltip is reported. Grep for other `<Tooltip` usages
  before finishing — if other Recharts charts exist with the same `contentStyle`
  pattern, apply the same fix for consistency (currently only `dashboard.tsx`
  references `Tooltip` from `recharts`).
- No backend, schema, or API changes. No new tests strictly required, but a quick
  manual visual check covers it.
