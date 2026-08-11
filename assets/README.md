# quantbot — brand assets

The mark is a **robot head whose eyes are candlesticks** — a green (up) and a rose
(down) candle — reading at once as "trading bot". Sky-blue antenna is the accent.

## Files

| File | Use |
|---|---|
| `logo.svg` / `logo.png` / `logo@2x.png` | Horizontal lockup (mark + wordmark) for **light** backgrounds — README, docs, site header. |
| `logo-dark.svg` / `logo-dark.png` / `logo-dark@2x.png` | Same lockup for **dark** backgrounds. |
| `avatar.svg` / `avatar-512.png` / `avatar-1024.png` | Rounded-square badge — **Telegram bot profile picture** (upload `avatar-512.png` to @BotFather → Edit Bot → Botpic). |
| `icon.svg` / `icon-256.png` / `icon-512.png` | Transparent mark only, no background. |
| `favicon.ico` / `favicon.png` (32) / `apple-touch-icon.png` (180) | Web favicons. |

## Palette

| Token | Hex |
|---|---|
| Ink / head | `#0F1B30` → `#1B2A46` |
| Border / ears | `#33507F` |
| Accent (antenna, "bot") | `#38BDF8` |
| Up candle | `#22C55E` → `#4ADE80` |
| Down candle | `#F43F5E` → `#FB7185` |

Type: SF Pro Display / Inter, weight 700 for the wordmark; SF Mono for the tagline.

## Regenerating PNGs

SVGs are the source of truth. Re-render with:

```bash
rsvg-convert -w 512 -h 512 avatar.svg -o avatar-512.png
rsvg-convert -h 220 logo.svg -o logo.png
```
