from __future__ import annotations

import unittest

from pawmarvel_generator.generation_contract import (
    provider_request_parameters,
    validate_provider_model,
)
from pawmarvel_generator.image_size import validate_generation_size


class GenerationContractTests(unittest.TestCase):
    def test_openai_gpt_image_2_request_is_closed(self) -> None:
        self.assertEqual(
            provider_request_parameters(
                provider="openai",
                model="gpt-image-2",
                size="816x816",
                quality="medium",
            ),
            {
                "quality": "medium",
                "size": "816x816",
                "background": "transparent",
                "output_format": "png",
                "n": 1,
            },
        )

    def test_older_openai_model_pins_input_fidelity(self) -> None:
        request = provider_request_parameters(
            provider="openai",
            model="gpt-image-1.5",
            size="1024x1024",
            quality="high",
        )
        self.assertEqual(request["input_fidelity"], "high")

    def test_openai_gpt_image_25_supports_extended_quality(self) -> None:
        self.assertEqual(
            provider_request_parameters(
                provider="openai",
                model="gpt-image-2.5-sunburst",
                size="1536x864",
                quality="max",
            ),
            {
                "quality": "max",
                "size": "1536x864",
                "background": "transparent",
                "output_format": "png",
                "n": 1,
            },
        )

    def test_gpt_image_2_rejects_gpt_image_25_only_quality(self) -> None:
        with self.assertRaisesRegex(ValueError, "not supported"):
            provider_request_parameters(
                provider="openai",
                model="gpt-image-2",
                size="1024x1024",
                quality="xhigh",
            )

    def test_rejects_nonexistent_gpt_image_25_family_alias(self) -> None:
        with self.assertRaisesRegex(ValueError, "not an API model ID"):
            validate_provider_model(provider="openai", model="gpt-image-2.5")

    def test_gpt_image_25_uses_custom_dimension_contract(self) -> None:
        self.assertEqual(
            validate_generation_size(
                "1536x864", model="gpt-image-2.5-flare"
            ),
            "1536x864",
        )

    def test_gemini_request_uses_native_size_and_aspect_ratio(self) -> None:
        self.assertEqual(
            provider_request_parameters(
                provider="gemini",
                model="gemini-3.1-flash-image",
                size="816x816",
                quality="high",
            ),
            {
                "response_format": {
                    "type": "image",
                    "aspect_ratio": "1:1",
                    "image_size": "1K",
                }
            },
        )


if __name__ == "__main__":
    unittest.main()
