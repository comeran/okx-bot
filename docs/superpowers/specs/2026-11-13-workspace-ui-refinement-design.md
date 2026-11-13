# Quant workspace UI refinement

## Intent and direction

The user requested optimization of the entire application and selected “按推荐方向直接优化”: a professional quant workspace with light content, dark navigation, teal emphasis, and compact data/form layouts across all six routes. This is an incremental refinement of the existing Vue/Element Plus application, including its uncommitted authentication fix.

## Approaches

- Selected: light analytical workspace with dark forest navigation and a restrained teal accent. It retains readable tables and the current interaction model while improving density and hierarchy.
- Full dark trading terminal: a stronger theme change requiring chart/editor recoloring and more extensive contrast review; not selected.
- Layout-only changes with the current palette: lower visual impact and leaves the weak blue/white contrast unresolved; not selected.

## Design

- Use a local Avenir/PingFang font stack, tabular figures, deep ink headings, pale neutral canvas, white surfaces, and a contrast-safe teal primary color. No font downloads, new packages, decorative financial charts, or fabricated values.
- Navigation gains a compact brand mark, recognizable route icons, clear active indication, and a stable desktop sidebar. The header becomes a quiet workspace breadcrumb with distinct local/provider status and a compact locale control. Include a keyboard skip link and an expanded state on the mobile menu button.
- Keep one prominent page title. Standardize page spacing, section headings, card boundaries, control height, table headers, numeric alignment, and subdued empty states. Respect reduced motion.
- Keep authentication failures persistent and fully readable, including environment and safe provider code. Replace the large repeated explanatory paragraphs with a compact status strip and a link to Settings. Never make a disconnected or failed state look healthy.
- Dashboard metrics form a dense responsive grid; Backtest removes tall equal-height nested form panels; Market and Trades use aligned query controls; Strategies preserves its list/editor flow; Settings retains all six sections and safe-area save actions.
- Backtest history failures receive an inline persistent error/retry state rather than looking like an empty table. All other changes remain presentational.

## Constraints and acceptance

- Preserve all uncommitted authentication work, credential persistence, account scoping, API/WS contracts, trading readiness, strategy lifecycle, backtest execution, and dirty-form guards.
- Do not edit backend files, credentials, stored account data, or start strategies. No commit, push, or merge in this task.
- Keep Simplified Chinese and English synchronized. Preserve the existing missing-value convention `—` and meaningful financial colors.
- Verify six routes at desktop width and representative narrow/tablet widths. Only labeled data-table regions may scroll horizontally; controls, notices, actions, and charts must remain usable.
- Existing frontend regressions, new semantic/inline-error tests, TypeScript, production build, and whitespace checks must pass. Independently review the final changes.
