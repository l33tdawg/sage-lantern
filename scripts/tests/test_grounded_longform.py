"""Deterministic grounded long-form composer tests; no model or device calls."""
from __future__ import annotations

import importlib.util
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "software/npu/grounded_longform.py"
SPEC = importlib.util.spec_from_file_location("grounded_longform_test", MODULE_PATH)
COMPOSER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(COMPOSER)

FACTS = [
    "The North Quay ferry departs at 06:40.",
    "The crossing takes 35 minutes.",
    "The last return is at 19:10.",
    "The boat carries 24 passengers.",
    "Bikes travel free.",
    "Tickets are sold at the harbour kiosk.",
    "Sunday service runs only when wind is below 20 knots.",
    "The crew briefing is at 06:10.",
]


def paragraphs(text):
    return [part for part in re.split(r"\n\s*\n", text.strip()) if part.strip()]


def request(facts, count="three", extra=""):
    return [{
        "role": "user",
        "content": (
            f"Write exactly {count} paragraphs for the fictional North Quay ferry "
            "operations note. Use only these facts: " + "; ".join(facts) +
            ". Cover the timetable, capacity and rules. Do not add any other detail." +
            extra
        ),
    }]


class GroundedLongformTests(unittest.TestCase):
    def test_three_paragraphs_preserve_every_fact(self):
        text = COMPOSER.compose_grounded_longform(request(FACTS))
        self.assertIsNotNone(text)
        self.assertEqual(len(paragraphs(text)), 3)
        for fact in FACTS:
            self.assertIn(fact.rstrip(".").lower(), text.lower())
        self.assertGreaterEqual(len(text.split()), 150)
        self.assertNotRegex(text, r"(?m)^\s*(#{1,6}\s|[-*+]\s)")
        self.assertNotIn("**", text)

    def test_four_paragraphs(self):
        text = COMPOSER.compose_grounded_longform(request(FACTS, count="four"))
        self.assertIsNotNone(text)
        self.assertEqual(len(paragraphs(text)), 4)
        for fact in FACTS:
            self.assertIn(fact.rstrip(".").lower(), text.lower())

    def test_missing_detail_instruction_is_acknowledged(self):
        text = COMPOSER.compose_grounded_longform(request(
            FACTS[:4], extra=" If the volunteer rota is not supplied, say so."))
        self.assertIsNotNone(text)
        self.assertIn("not supply", text.lower())
        self.assertEqual(len(paragraphs(text)), 3)

    def test_ordinary_question_returns_none(self):
        self.assertIsNone(COMPOSER.compose_grounded_longform([
            {"role": "user", "content": "What does photosynthesis mean?"},
        ]))

    def test_action_request_returns_none(self):
        self.assertIsNone(COMPOSER.compose_grounded_longform([
            {"role": "user",
             "content": "Save that the intake label is teal. Use these facts: "
                        "The intake label is teal; the desk closes at 17:00; "
                        "the room is B2; the lead is Mira."},
        ]))

    def test_unsupplied_number_is_not_invented(self):
        text = COMPOSER.compose_grounded_longform(request(FACTS))
        self.assertIsNotNone(text)
        self.assertNotIn("25 passengers", text)
        self.assertNotIn("30 minutes", text)

    def test_correction_uses_new_values_and_omits_old(self):
        messages = [
            {"role": "user",
             "content": "The ferry departs at 06:40 and the crossing takes 35 minutes. "
                        "It carries 24 passengers, bikes travel free, and the last return is 19:10."},
            {"role": "assistant",
             "content": "The ferry leaves at 06:40 and the crossing takes 35 minutes. "
                        "It carries 24 passengers, bikes travel free, and the last return is 19:10."},
            {"role": "user",
             "content": "Correction: the departure is now 07:15 and the crossing takes 40 minutes. "
                        "Keep the other facts unchanged. Write exactly three paragraphs covering the "
                        "corrected timetable, capacity and bikes, then the last return. "
                        "Do not mention the old times."},
        ]
        text = COMPOSER.compose_grounded_longform(messages)
        self.assertIsNotNone(text)
        self.assertEqual(len(paragraphs(text)), 3)
        self.assertIn("07:15", text)
        self.assertIn("40 minutes", text)
        self.assertNotIn("06:40", text)
        self.assertNotIn("35 minutes", text)
        self.assertIn("24 passengers", text)
        self.assertIn("19:10", text)

    def test_missing_detail_question_fails_closed(self):
        text = COMPOSER.compose_grounded_longform([{
            "role": "user",
            "content": (
                "The fictional Hilltop observatory opens the dome at 19:30, uses a "
                "0.4 m telescope, and keeps the cloud log in a binder. What is the "
                "queue order for tonight? Answer in one short paragraph and do not "
                "invent names or a rota."
            ),
        }])
        self.assertIsNotNone(text)
        self.assertIn("does not answer", text)
        self.assertIn("not supplied", text)
        self.assertNotIn("first", text.lower())


if __name__ == "__main__":
    unittest.main()
