# TCSS reference for Icarus

TCSS is Textual's CSS dialect. It styles the widget tree at runtime; changes are visible through snapshots and the real terminal.

## File and token structure

- `apps/tui/src/styles/tokens.tcss` owns tokens; base, screens and widgets own their respective rules. App.get_css_variables supplies the tokens to each stylesheet; file order alone does not share variables.
- A widget's `DEFAULT_CSS` owns only what the widget needs to look right in isolation.
- Declare tokens once at the top of the stylesheet:

```tcss
$icarus-background: #0c0c0e;
$icarus-surface: #131315;
$icarus-panel: #1c1c1f;
$icarus-border: #4b4b51;
$icarus-text: #f1f1f2;
$icarus-muted: #97979e;
$icarus-accent: #e0a8b6;
$icarus-green: #75bd92;
$icarus-red: #db737f;
```

Use tokens rather than repeated hex values. Built-in theme tokens are also available: `$primary`, `$secondary`, `$accent`, `$background`, `$foreground`, `$surface`, `$panel`, `$boost`, `$warning`, `$error`, `$success`, `$text`, `$text-muted`, `$text-disabled`, `$text-primary`, `$text-secondary`, `$text-warning`, `$text-error`, `$text-success`, `$text-accent`, plus shade variants such as `$primary-darken-1`.

The app is dark-first. Use `:dark` / `:light` only for genuinely theme-dependent rules; otherwise use the Icarus tokens.

## Selectors

```tcss
ConversationView { ... }          /* widget type */
#conversation { ... }             /* stable ID */
.-narrow #conversation { ... }    /* state class + descendant */
#composer:focus { ... }           /* pseudo-class */
#composer:focus-within { ... }    /* a child has focus */
#turn-rail > TurnExcerpts { ... } /* direct child */
#conversation { & AssistantMessage { ... } }  /* nesting with & */
```

Guidelines:

- Use IDs for stable layout anchors and test hooks.
- Use classes for variants and runtime states (`-narrow`, `-short`, `-compact-logo`, `-running`, `-failed`).
- Prefer styling a component at its own boundary over long descendant chains.
- Valid pseudo-classes include `:focus`, `:blur`, `:focus-within`, `:hover`, `:disabled`, `:enabled`, `:dark`, `:light`, `:first-child`, `:last-child`, `:first-of-type`, `:last-of-type`, `:odd`, `:even`, `:empty`, `:can-focus`, `:ansi`, `:inline`, `:nocolor`.

## Layout

```tcss
Screen {
    layout: vertical;   /* vertical | horizontal | grid | stream */
}

#conversation-shell {
    layout: horizontal;
    height: 1fr;
}

#status-bar {
    dock: bottom;       /* top | bottom | left | right */
    height: 1;
}
```

- `display: block | none` removes/restores a widget from layout.
- `visibility: visible | hidden` keeps layout space but hides rendering.
- `position: relative | absolute` plus `offset: x y` for overlays.
- `layers` / `layer` order the stacking context; `overlay: screen` makes an element escape its parent's clipping.
- `align: left|center|right top|middle|bottom` positions children inside a container.
- `content-align` positions a widget's own content.
- Grid: `layout: grid; grid-size: 2 3; grid-columns: 1fr 2fr; grid-rows: auto 1fr; grid-gutter: 0 1;` and per-child `column-span` / `row-span`.
- `constrain: inside|inflect|none` keeps overlays within the screen.

## Sizing

```tcss
#app-title   { height: 1; }
#conversation { width: 1fr; height: 1fr; }
#turn-rail    { width: 2; height: 1fr; }
#composer     { height: auto; max-height: 8; }
#workspace-label { width: 1fr; min-width: 0; }
```

- `1fr` shares remaining space; fixed cell counts (`1`, `2`) are for chrome.
- `auto` sizes to content. `%` is relative to the parent.
- `min-width: 0` lets flex children shrink so text can ellipsize instead of forcing overflow.
- `max-height` / `max-width` prevent runaway growth.
- `box-sizing: border-box | content-box` controls whether padding/border count toward the declared size.
- Text overflow: `text-overflow: clip | fold | ellipsis` and `text-wrap: wrap | nowrap`.

## Spacing and borders

```tcss
#conversation {
    padding: 1 2;
    border: round $icarus-border;
    outline: solid $icarus-accent;
}
```

- `padding: 1 2` is vertical/horizontal; four values follow top/right/bottom/left.
- `margin` uses the same shorthand.
- Border styles: `ascii`, `blank`, `dashed`, `double`, `heavy`, `hidden`, `hkey`, `inner`, `none`, `outer`, `panel`, `round`, `solid`, `tall`, `thick`, `vkey`, `wide`.
- `border-title-align`, `border-title-color`, `border-title-background`, `border-title-style` and the `subtitle-*` equivalents style border labels.
- `keyline` draws an inner focus/accent line; use sparingly.

## Color and text

- `color`, `background`, `background-tint`, `opacity`, `text-opacity`.
- `text-style: bold italic underline reverse strike`.
- `text-align: start | end | left | right | center | justify`.
- `link-color`, `link-color-hover`, `link-style`, `link-style-hover`.

Do not encode state with color alone. Pair color with a label, icon, or text weight.

## Scrolling

```tcss
#conversation {
    overflow-y: auto;
    overflow-x: hidden;
    scrollbar-size-vertical: 1;
    scrollbar-color: $icarus-tool;
    scrollbar-color-hover: $icarus-user;
    scrollbar-background: $icarus-background;
    scrollbar-gutter: stable;
}
```

- `overflow-x/y: auto | scroll | hidden`.
- `scrollbar-size`, `scrollbar-size-vertical`, `scrollbar-size-horizontal`.
- `scrollbar-color`, `scrollbar-color-hover`, `scrollbar-color-active`.
- `scrollbar-background`, `scrollbar-background-hover`, `scrollbar-background-active`, `scrollbar-corner-color`.
- `scrollbar-visibility: visible | hidden`.
- Keep one scroll owner per region. Tool/long-message inner regions are intentional; define wheel edge handoff and independent follow state.

## Focus and hover

```tcss
#composer:focus {
    border: round $icarus-accent;
}

#turn-rail:focus {
    background: $icarus-panel;
}

#conversation:focus-within {
    border: round $icarus-border;
}
```

- Always show focus explicitly for keyboard-operable widgets.
- Use `:hover` for pointer affordances, but never make hover the only way to discover an action.
- `:disabled` should change both color and affordance (for example, hide a key hint).

## Responsive behavior

Textual has no media queries. Toggle semantic classes in `on_resize`:

```python
def _update_responsive_classes(self, width: int, height: int) -> None:
    self.screen.set_class(width <= 70, "-narrow")
    self.screen.set_class(width <= 88, "-compact-logo")
    self.screen.set_class(height <= 12, "-short")
```

Then style the classes:

```tcss
#app-title { display: block; }
.-compact-logo #app-title { display: none; }
.-narrow #turn-rail { display: none; }
.-short #queue-panel { max-height: 2; }
```

Check at least: wide (120×40), narrow (70×24), very narrow (60×20), short (100×12).

## Transition and animation

- `transition: background 150ms`, `transition: opacity 200ms in_out_cubic`.
- Keep animation short and non-blocking. Streaming output already changes frequently; avoid animating text layout.

## Debugging TCSS

1. Run the app with `textual run --dev` and use `textual console` for CSS errors.
2. If a rule does not apply, inspect specificity, the widget's `DEFAULT_CSS`, and whether an inline `styles.*` assignment overrides it.
3. Prefer `textual run --dev` over guessing; TCSS errors are reported at parse time.
4. After a layout change, capture actual App frames using scripts/capture_preview.py at wide/narrow/short sizes and inspect them; no maintained SVG baseline suite exists.
