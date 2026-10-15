# The UI kit

The owner's sheet (2026-10-14, `docs/design/ui-kit-components.webp`) is the look of every control of the panel. **Everything new is built from these elements**: no new button style, no new field style, no new menu - take the class that exists.

| Element | How it is written | Look |
|---|---|---|
| Primary button | `<button class="pri">` (or `.primary`, `.cta`) | the blue gradient with a glow - the ONE main action of a place |
| Secondary button | a plain `<button>` | dark glass, a light edge, pressed in |
| Ghost button | `<button class="ghost">` | no box until hover |
| Destructive button | `<button class="danger">` (or `.del`) | red on a red tint |
| Icon button | a plain button with an icon inside | square, like the secondary |
| Fields | `input`, `select`, `textarea` | a dark well, a blue ring on focus |
| Segmented control | `.tabs` (tabs of a page) or `.pg-useg` (a choice of one: range, kind) | a dark track, the open one lifted |
| Chips | `.pill`, `.dns-chip`, `.pg-kind-chip` | small, rounded |
| Menu that opens | `nkMenu(btn, items, ev)` with `labItem / labGrp / labSep` | ONE column, groups, a line between them, the last (delete) red |

Rules:

1. `web/uikit.css` is loaded LAST and sets only colour, border, shadow, radius and the focus ring. It never sets a width, height, padding or a place, so a page does not move when the kit changes. A size belongs to the page's own css.
2. Every list of options that opens is the grouped one-column menu (`nkMenu`). A native `<select>` is for a plain choice of values inside a form, not for a list of actions.
3. One primary button per place. A destructive action is red and is the last item of its group.
4. Colours are the tokens of `:root` in `uikit.css` (`--uk-blue`, `--uk-red`, `--uk-field`, `--uk-line`, ...). Do not write a colour of your own.
5. A test (`tests/test_web_menu.py`) checks that the kit is registered, is the last stylesheet and sets no size.