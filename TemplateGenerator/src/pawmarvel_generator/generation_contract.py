"""Pure provider request semantics shared by generation and bundle contracts."""

from __future__ import annotations

from typing import Any

from .image_size import is_gpt_image_2


GEMINI_ASPECT_RATIOS = (
    "1:8",
    "1:4",
    "2:3",
    "3:4",
    "4:5",
    "1:1",
    "5:4",
    "4:3",
    "3:2",
    "4:1",
    "8:1",
    "9:16",
    "16:9",
    "21:9",
)


def gemini_aspect_ratio(size: str) -> str | None:
    if size == "auto":
        return None
    width, height = (int(value) for value in size.split("x", 1))
    target = width / height

    def distance(value: str) -> float:
        numerator, denominator = (int(part) for part in value.split(":", 1))
        return abs((numerator / denominator) - target)

    return min(GEMINI_ASPECT_RATIOS, key=distance)


def gemini_image_size(size: str, model: str) -> str | None:
    if size == "auto":
        return None
    if "flash-lite-image" in model:
        return "1K"
    width, height = (int(value) for value in size.split("x", 1))
    longest = max(width, height)
    if longest <= 512:
        return "512"
    if longest <= 1024:
        return "1K"
    if longest <= 2048:
        return "2K"
    return "4K"


def provider_request_parameters(
    *, provider: str, model: str, size: str, quality: str
) -> dict[str, Any]:
    """Return only the fields passed to the selected provider's image API."""
    if provider == "openai":
        request: dict[str, Any] = {
            "quality": quality,
            "size": size,
            "background": "transparent",
            "output_format": "png",
            "n": 1,
        }
        if not is_gpt_image_2(model):
            request["input_fidelity"] = "high"
        return request
    if provider == "gemini":
        response_format: dict[str, str] = {"type": "image"}
        aspect_ratio = gemini_aspect_ratio(size)
        image_size = gemini_image_size(size, model)
        if aspect_ratio is not None:
            response_format["aspect_ratio"] = aspect_ratio
        if image_size is not None:
            response_format["image_size"] = image_size
        return {"response_format": response_format}
    raise ValueError(f"unsupported image provider: {provider}")
