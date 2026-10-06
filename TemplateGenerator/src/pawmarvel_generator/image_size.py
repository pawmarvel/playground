from __future__ import annotations

import re
from dataclasses import dataclass


GPT_IMAGE_2_MAX_EDGE = 3840
GPT_IMAGE_2_MIN_PIXELS = 655_360
GPT_IMAGE_2_MAX_PIXELS = 8_294_400
GPT_IMAGE_2_EDGE_MULTIPLE = 16
GPT_IMAGE_2_MAX_ASPECT_RATIO = 3.0
GPT_IMAGE_2_PATTERN = re.compile(r"gpt-image-2(?:-\d{4}-\d{2}-\d{2})?")
GPT_IMAGE_25_PATTERN = re.compile(
    r"gpt-image-2\.5-(?:sunburst|flare)(?:-\d{4}-\d{2}-\d{2})?"
)


class ImageSizeError(ValueError):
    """An image-generation size is malformed or unsupported by the model."""


@dataclass(frozen=True)
class ImageSize:
    width: int
    height: int

    @property
    def pixels(self) -> int:
        return self.width * self.height

    def api_value(self) -> str:
        return f"{self.width}x{self.height}"

    def to_dict(self) -> dict[str, int]:
        return {"width": self.width, "height": self.height}


def parse_image_size(value: str, label: str = "size") -> ImageSize:
    if not isinstance(value, str) or not re.fullmatch(r"[1-9]\d*x[1-9]\d*", value):
        raise ImageSizeError(f"{label} must use WIDTHxHEIGHT")
    width, height = (int(part) for part in value.split("x", 1))
    return ImageSize(width, height)


def is_gpt_image_2(model: str) -> bool:
    """Return whether a model uses the GPT Image 2/2.5 size contract."""
    return bool(
        GPT_IMAGE_2_PATTERN.fullmatch(model)
        or GPT_IMAGE_25_PATTERN.fullmatch(model)
    )


def is_gpt_image_25(model: str) -> bool:
    """Return whether a model is an official GPT Image 2.5 variant/snapshot."""
    return bool(GPT_IMAGE_25_PATTERN.fullmatch(model))


def validate_gpt_image_2_size(size: ImageSize, label: str = "size") -> ImageSize:
    if max(size.width, size.height) > GPT_IMAGE_2_MAX_EDGE:
        raise ImageSizeError(
            f"{label} maximum edge must not exceed {GPT_IMAGE_2_MAX_EDGE}px"
        )
    if (
        size.width % GPT_IMAGE_2_EDGE_MULTIPLE
        or size.height % GPT_IMAGE_2_EDGE_MULTIPLE
    ):
        raise ImageSizeError(
            f"{label} width and height must both be multiples of "
            f"{GPT_IMAGE_2_EDGE_MULTIPLE}px"
        )
    ratio = max(size.width, size.height) / min(size.width, size.height)
    if ratio > GPT_IMAGE_2_MAX_ASPECT_RATIO:
        raise ImageSizeError(f"{label} long-to-short edge ratio must not exceed 3:1")
    if size.pixels < GPT_IMAGE_2_MIN_PIXELS:
        raise ImageSizeError(
            f"{label} must contain at least {GPT_IMAGE_2_MIN_PIXELS:,} pixels"
        )
    if size.pixels > GPT_IMAGE_2_MAX_PIXELS:
        raise ImageSizeError(
            f"{label} must contain no more than {GPT_IMAGE_2_MAX_PIXELS:,} pixels"
        )
    return size


def closest_gpt_image_2_size(size: ImageSize) -> ImageSize:
    """Return the API-valid canvas with the smallest relative edge change."""
    candidates: list[ImageSize] = []
    for width in range(
        GPT_IMAGE_2_EDGE_MULTIPLE,
        GPT_IMAGE_2_MAX_EDGE + 1,
        GPT_IMAGE_2_EDGE_MULTIPLE,
    ):
        for height in range(
            GPT_IMAGE_2_EDGE_MULTIPLE,
            GPT_IMAGE_2_MAX_EDGE + 1,
            GPT_IMAGE_2_EDGE_MULTIPLE,
        ):
            candidate = ImageSize(width, height)
            try:
                validate_gpt_image_2_size(candidate, "candidate size")
            except ImageSizeError:
                continue
            candidates.append(candidate)
    if not candidates:
        raise ImageSizeError("no API-valid GPT Image 2/2.5 canvas is available")
    return min(
        candidates,
        key=lambda candidate: (
            ((candidate.width - size.width) / size.width) ** 2
            + ((candidate.height - size.height) / size.height) ** 2,
            abs(candidate.pixels - size.pixels),
            candidate.width,
            candidate.height,
        ),
    )


def validate_generation_size(
    value: str,
    *,
    model: str,
    label: str = "--size",
    allow_auto: bool = True,
) -> str:
    if value == "auto":
        if allow_auto:
            return value
        raise ImageSizeError(f"{label} must use an explicit WIDTHxHEIGHT value")
    parsed = parse_image_size(value, label)
    if is_gpt_image_2(model):
        validate_gpt_image_2_size(parsed, label)
    return parsed.api_value()
