from __future__ import annotations

import unittest

from pawmarvel_generator.generation_contract import provider_request_parameters


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
