"""Discord-specific utility functions.

This module contains utilities extracted from discord_bot.py that are
specifically for Discord integration, such as image resizing for vision
and timestamp formatting.

These utilities are Discord-specific because:
- resize_image_for_vision: Optimizes images for Discord's attachment limits
  and Claude's vision processing requirements
- format_discord_timestamp: Converts datetimes to Discord's relative timestamp
  format (<t:timestamp:R>) for native Discord rendering
"""

from __future__ import annotations

import io
from datetime import datetime
from typing import TYPE_CHECKING

from PIL import Image

if TYPE_CHECKING:
    from zoneinfo import ZoneInfo


# Default maximum dimension for resized images (Claude's recommended ~1.15MP)
MAX_IMAGE_DIMENSION = 1568


def resize_image_for_vision(
    image_bytes: bytes, max_dimension: int = MAX_IMAGE_DIMENSION
) -> tuple[bytes, str]:
    """Resize an image to fit within max_dimension while preserving aspect ratio.

    This function prepares images for LLM vision processing by:
    - Resizing large images to fit within Claude's recommended dimensions
    - Converting to JPEG for efficient compression (except small files)
    - Handling transparency by compositing onto white background

    Args:
        image_bytes: Raw image bytes from Discord attachment or other source
        max_dimension: Maximum pixels on longest edge (default: 1568)

    Returns:
        Tuple of (resized image bytes, media_type string like "image/jpeg")
    """
    with Image.open(io.BytesIO(image_bytes)) as img:
        # Get original dimensions
        orig_width, orig_height = img.size

        # Check if resize is needed
        if orig_width <= max_dimension and orig_height <= max_dimension:
            # Image is already small enough, but still convert to JPEG for consistency
            # (unless it's already a small PNG/GIF that should stay as-is)
            if len(image_bytes) < 500_000:  # < 500KB, keep original format
                # Determine format from image
                img_format = img.format or "PNG"
                media_type = f"image/{img_format.lower()}"
                if media_type == "image/jpeg":
                    media_type = "image/jpeg"
                return image_bytes, media_type

        # Calculate new dimensions maintaining aspect ratio
        if orig_width > orig_height:
            new_width = max_dimension
            new_height = int(orig_height * (max_dimension / orig_width))
        else:
            new_height = max_dimension
            new_width = int(orig_width * (max_dimension / orig_height))

        # Convert to RGB if necessary (for JPEG output)
        if img.mode in ("RGBA", "P", "LA"):
            # Create white background for transparency
            background = Image.new("RGB", img.size, (255, 255, 255))
            if img.mode == "P":
                img = img.convert("RGBA")
            background.paste(img, mask=img.split()[-1] if img.mode == "RGBA" else None)
            img = background
        elif img.mode != "RGB":
            img = img.convert("RGB")

        # Resize with high-quality resampling
        resized = img.resize((new_width, new_height), Image.Resampling.LANCZOS)

        # Save to bytes as JPEG with good quality
        output = io.BytesIO()
        resized.save(output, format="JPEG", quality=85, optimize=True)
        return output.getvalue(), "image/jpeg"


def format_discord_timestamp(dt: datetime, style: str = "R") -> str:
    """Format a datetime as a Discord timestamp string.

    Discord timestamps render natively in the client, automatically
    adjusting to each user's local timezone and locale.

    Args:
        dt: A datetime object (timezone-aware or naive)
        style: Discord timestamp style:
            - "t" = Short time (e.g., 9:41 PM)
            - "T" = Long time (e.g., 9:41:30 PM)
            - "d" = Short date (e.g., 06/20/2021)
            - "D" = Long date (e.g., June 20, 2021)
            - "f" = Short date/time (e.g., June 20, 2021 9:41 PM)
            - "F" = Long date/time (e.g., Sunday, June 20, 2021 9:41 PM)
            - "R" = Relative (e.g., 2 hours ago) [default]

    Returns:
        Discord timestamp format string like "<t:1624242090:R>"
    """
    # Convert to Unix timestamp
    if dt.tzinfo is None:
        # Assume naive datetimes are UTC
        unix_ts = int(dt.timestamp())
    else:
        unix_ts = int(dt.timestamp())

    return f"<t:{unix_ts}:{style}>"


def format_user_timezone_timestamp(dt: datetime, timezone_name: str) -> str:
    """Format a datetime in the user's timezone as a human-readable string.

    This is a fallback for contexts where Discord's native timestamp
    rendering isn't available.

    Args:
        dt: A datetime object (should be timezone-aware)
        timezone_name: IANA timezone name (e.g., "America/New_York")

    Returns:
        Formatted string like "10:43 PM EST"
    """
    from zoneinfo import ZoneInfo

    try:
        tz = ZoneInfo(timezone_name)
        local_dt = dt.astimezone(tz)
        return local_dt.strftime("%-I:%M %p %Z")
    except Exception:
        return dt.strftime("%H:%M UTC")


__all__ = [
    "MAX_IMAGE_DIMENSION",
    "resize_image_for_vision",
    "format_discord_timestamp",
    "format_user_timezone_timestamp",
]
