# Game Mode — Sprite & Asset Guidelines

Art asset specifications for the Clara games site (games.mypalclara.com).

## General Rules

- PNG format, transparency via alpha channel
- No padding, no margins, no gutters between frames/cells
- Pixel-perfect alignment — frame boundaries must be exact
- Scale with nearest-neighbor (no anti-aliasing) to preserve pixel art crispness
- Consistent palette across all assets for visual cohesion

## Clara — Animated Sprite Sheets

Each animation is a **horizontal strip** — all frames in a single row, left to right.

```
┌───────┬───────┬───────┬───────┐
│ Frame │ Frame │ Frame │ Frame │
│   0   │   1   │   2   │   3   │
└───────┴───────┴───────┴───────┘
← frame_width →

Total PNG width  = frame_width × frame_count
Total PNG height = frame_height
```

### File Structure

```
sprites/clara/
  manifest.json
  idle.png
  talk.png
  happy.png
  nervous.png
  smug.png
  surprised.png
  defeated.png
```

### manifest.json

```json
{
  "frame_width": 128,
  "frame_height": 192,
  "animations": {
    "idle":      { "file": "idle.png",      "frames": 4, "fps": 2, "loop": true },
    "talk":      { "file": "talk.png",      "frames": 4, "fps": 8, "loop": true },
    "happy":     { "file": "happy.png",     "frames": 2, "fps": 2, "loop": true },
    "nervous":   { "file": "nervous.png",   "frames": 3, "fps": 3, "loop": true },
    "smug":      { "file": "smug.png",      "frames": 2, "fps": 2, "loop": true },
    "surprised": { "file": "surprised.png", "frames": 2, "fps": 1, "loop": false },
    "defeated":  { "file": "defeated.png",  "frames": 2, "fps": 2, "loop": true }
  }
}
```

- `frame_width` / `frame_height` — set to whatever resolution you choose
- `loop: false` — play once then return to idle
- All animation files must use the same `frame_width` and `frame_height`
- Transparent background (PNG alpha)

## Cards — Grid Layout

52 cards + card back arranged in a grid: **13 columns × 5 rows**.

```
         A    2    3    4    5    6    7    8    9   10    J    Q    K
      ┌────┬────┬────┬────┬────┬────┬────┬────┬────┬────┬────┬────┬────┐
  ♠   │    │    │    │    │    │    │    │    │    │    │    │    │    │  Row 0
      ├────┼────┼────┼────┼────┼────┼────┼────┼────┼────┼────┼────┼────┤
  ♥   │    │    │    │    │    │    │    │    │    │    │    │    │    │  Row 1
      ├────┼────┼────┼────┼────┼────┼────┼────┼────┼────┼────┼────┼────┤
  ♦   │    │    │    │    │    │    │    │    │    │    │    │    │    │  Row 2
      ├────┼────┼────┼────┼────┼────┼────┼────┼────┼────┼────┼────┼────┤
  ♣   │    │    │    │    │    │    │    │    │    │    │    │    │    │  Row 3
      ├────┼────┼────┼────┼────┼────┼────┼────┼────┼────┼────┼────┼────┤
 Back │    │    │    │    │    │    │    │    │    │    │    │    │    │  Row 4
      └────┴────┴────┴────┴────┴────┴────┴────┴────┴────┴────┴────┴────┘
```

- Suit = row (0–3: spades, hearts, diamonds, clubs)
- Rank = column (0–12: A, 2, 3, ... K)
- Card back at row 4, col 0 (rest of row 4 empty)
- No padding between cards

### cards.json

```json
{
  "card_width": 64,
  "card_height": 89,
  "suits": ["spades", "hearts", "diamonds", "clubs"],
  "ranks": ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"],
  "back": { "row": 4, "col": 0 }
}
```

### File Structure

```
sprites/games/blackjack/
  cards.png
  cards.json
  table.png          # table/felt background
```

## Checkers Pieces — Horizontal Strip

4 piece variants in a single row.

```
┌───────┬───────┬───────┬───────┐
│  Red  │  Red  │ Black │ Black │
│ normal│  king │ normal│  king │
└───────┴───────┴───────┴───────┘
  col 0   col 1   col 2   col 3
```

- Single row, transparent background
- Board is a separate file (`board.png`) — just the 8×8 grid, no pieces baked in

### pieces.json

```json
{
  "piece_width": 48,
  "piece_height": 48,
  "sprites": {
    "red":        { "col": 0 },
    "red_king":   { "col": 1 },
    "black":      { "col": 2 },
    "black_king": { "col": 3 }
  }
}
```

### File Structure

```
sprites/games/checkers/
  board.png
  pieces.png
  pieces.json
```
