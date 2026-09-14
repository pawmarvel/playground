from __future__ import annotations

import unittest

from pawmarvel_generator.personalization import (
    PersonalizationError,
    pet_name_policy,
    validate_pet_name,
)


class PersonalizationTests(unittest.TestCase):
    def test_normalizes_and_collapses_whitespace(self) -> None:
        policy = pet_name_policy(12)
        self.assertEqual(validate_pet_name("  Cafe\u0301\tDog  ", policy), "Café Dog")

    def test_accepts_multilingual_letters_and_common_apostrophe(self) -> None:
        policy = pet_name_policy(12)
        self.assertEqual(validate_pet_name("小白", policy), "小白")
        self.assertEqual(validate_pet_name("O’Malley", policy), "O’Malley")

    def test_rejects_too_long_or_unsupported_punctuation(self) -> None:
        policy = pet_name_policy(3)
        with self.assertRaisesRegex(PersonalizationError, "Unicode code points"):
            validate_pet_name("BUDDY", policy)
        with self.assertRaisesRegex(PersonalizationError, "outside"):
            validate_pet_name("A!", policy)

    def test_rejects_unknown_policy(self) -> None:
        policy = pet_name_policy(12)
        policy["length_unit"] = "bytes"
        with self.assertRaisesRegex(PersonalizationError, "unsupported"):
            validate_pet_name("PET", policy)


if __name__ == "__main__":
    unittest.main()
