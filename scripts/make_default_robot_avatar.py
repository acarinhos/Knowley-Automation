"""Draw the shared robot avatar (512x512 pixel art, transparent background) into data/default_robot.png."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

OUTPUT = Path(__file__).resolve().parents[1] / "data" / "default_robot.png"
SCALE = 16  # 32x32 art grid -> 512x512
PALETTE = {"o": (30, 36, 64, 255), "b": (112, 150, 214, 255), "l": (170, 200, 245, 255), "d": (70, 100, 160, 255),
           "e": (110, 240, 255, 255), "r": (255, 112, 140, 255), "y": (255, 214, 102, 255)}
ART = [
    "................................",
    "................................",
    "...............oooo.............",
    "...............oyyo.............",
    "................oo..............",
    "................oo..............",
    "........oooooooooooooooo........",
    ".......obbbbbbbbbbbbbbbbo.......",
    ".......oblllllllllllllllbo......",
    ".......oblooooooooooooolbo......",
    "......oobloeeeoooooeeeoolboo....",
    "......obbloeeeoooooeeeolbbbo....",
    "......obbloeeeoooooeeeolbbbo....",
    "......oobloooooooooooooolboo....",
    ".......oblllllllllllllllbo......",
    ".......obbbbrrrrrrrrbbbbbo......",
    ".......obbbbbbbbbbbbbbbbbo......",
    "........oooooooooooooooo........",
    "...............oo...............",
    "...........oooooooooooo.........",
    "..........obbbbbbbbbbbbo........",
    ".....oo...oblllllllllllbo...oo..",
    "....obbo..obdddyyyydddbo..obbo..",
    "....obbo..obdddyyyydddbo..obbo..",
    "....oddo..obbbbbbbbbbbbo..oddo..",
    ".....oo...obbbbbbbbbbbbo...oo...",
    "..........oooooooooooooo........",
    "............oo......oo...........",
    "...........obbo....obbo.........",
    "...........obbo....obbo.........",
    "..........oddddo..oddddo........",
    "...........oooo....oooo.........",
]


def main() -> int:
    width = len(ART)  # square canvas; rows are padded/cropped to it
    image = Image.new("RGBA", (width, len(ART)), (0, 0, 0, 0))
    for y, row in enumerate(ART):
        for x, char in enumerate(row.ljust(width, ".")[:width]):
            if char in PALETTE:
                image.putpixel((x, y), PALETTE[char])
    image.resize((width * SCALE, len(ART) * SCALE), Image.NEAREST).save(OUTPUT, optimize=True)
    print(f"Wrote {OUTPUT} ({width * SCALE}x{len(ART) * SCALE})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
