# Accessibility

Tatanka Charts aims to be usable with a keyboard, on small screens, and at larger text sizes. Where it falls short,
please say so in an issue (label it accessibility): it helps decide what to fix first.

## What works

- **Keyboard:** symbol and timeframe by typing on the chart, zoom and scroll with the arrow keys, Alt+A to create an
  alert, Alt+G to go to a date, the drawing tools' TradingView shortcuts, Esc to close dialogs and menus. The list is
  in the app (the keyboard shortcuts dialog).
- **Text size and colors:** the chart's font size, candle and background colors, line colors and widths are settings,
  with chart color themes to start from. The app itself is dark only.
- **Phone and tablet:** a layout of its own, large touch targets, long-press menus in place of right-click.
- **Alerts:** sounds and app notifications as well as on-screen popups.

## Known limits

- The chart itself is drawn on a canvas: prices, bars and indicator lines are not read out by screen readers. The
  watchlist, alerts, Plan tab, dialogs and the data window are ordinary page elements.
- Some studies show direction by color alone (up and down candles, delta, gap tables); the colors can be changed in
  their settings, but the default palette is red and green/blue.
- Dragging (drawings, alert lines) needs a pointer or touch; drawings can also be moved by editing their prices.

## Supported environments

Tested in current Edge and Chrome (Windows, Mac), Safari on iPhone (iOS 16.4 or later) and Chrome on Android. Firefox
and Safari on a Mac should work; say so in an issue if something doesn't.
