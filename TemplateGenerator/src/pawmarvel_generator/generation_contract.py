"""Pure provider request semantics shared by generation and bundle contracts."""

from __future__ import annotations

from typing import Any

from .image_size import is_gpt_image_2, is_gpt_image_25


BASE_GENERATION_QUALITIES = ("low", "medium", "high", "auto")
GPT_IMAGE_25_QUALITIES = (*BASE_GENERATION_QUALITIES, "xhigh", "max")
CLI_GENERATION_QUALITIES = GPT_IMAGE_25_QUALITIES


def validate_provider_model(*, provider: str, model: str) -> str:
    """Validate the provider/model pairing and GPT Image 2.5 variant name."""
    if provider == "openai":
        if model == "gpt-image-2.5":
            raise ValueError(
                "gpt-image-2.5 is not an API model ID; choose "
                "gpt-image-2.5-sunburst or gpt-image-2.5-flare"
            )
        if model.startswith("gpt-image-2.5-") and not is_gpt_image_25(model):
            raise ValueError(
                f"unsupported GPT Image 2.5 model ID {model!r}; choose "
                "gpt-image-2.5-sunburst or gpt-image-2.5-flare, optionally "
                "with an official YYYY-MM-DD snapshot suffix"
            )
        if not model.startswith("gpt-image-"):
            raise ValueError("OpenAI image generation requires a gpt-image-* model")
        return model
    if provider == "gemini":
        if not model.startswith("gemini-"):
            raise ValueError("Gemini image generation requires a gemini-* model")
        return model
    raise ValueError(f"unsupported image provider: {provider}")


def validate_generation_quality(*, provider: str, model: str, quality: str) -> str:
    """Validate a quality value against the selected provider/model contract."""
    validate_provider_model(provider=provider, model=model)
    allowed = (
        GPT_IMAGE_25_QUALITIES
        if provider == "openai" and is_gpt_image_25(model)
        else BASE_GENERATION_QUALITIES
    )
    if quality not in allowed:
        raise ValueError(
            f"quality {quality!r} is not supported by {model!r}; "
            f"choose one of {', '.join(allowed)}"
        )
    return quality


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
    validate_generation_quality(provider=provider, model=model, quality=quality)
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
