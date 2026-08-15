"""Provider token semantics, cache accounting, and transcript deduplication."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import spend  # noqa: E402


class TestUsageNormalization(unittest.TestCase):
    def test_claude_categories_are_disjoint(self):
        got = spend.normalize_usage({
            "input_tokens": 100,
            "cache_read_input_tokens": 40,
            "cache_creation_input_tokens": 10,
            "output_tokens": 20,
        }, provider="claude")
        self.assertEqual(got["uncached_input_tokens"], 100)
        self.assertEqual(got["cached_input_tokens"], 40)
        self.assertEqual(got["cache_creation_tokens"], 10)
        self.assertEqual(got["raw_total_tokens"], 170)
        self.assertEqual(got["effective_tokens"], 224)

    def test_codex_input_total_subtracts_cached_input_once(self):
        got = spend.normalize_usage({
            "input_tokens": 100,
            "input_tokens_details": {"cached_tokens": 40},
            "output_tokens": 20,
            "total_tokens": 120,
        }, provider="codex")
        self.assertEqual(got["uncached_input_tokens"], 60)
        self.assertEqual(got["cached_input_tokens"], 40)
        self.assertEqual(got["raw_total_tokens"], 120)
        self.assertEqual(got["effective_tokens"], 164)

    def test_missing_cache_telemetry_is_unavailable_not_zero(self):
        got = spend.normalize_usage(
            {"input_tokens": 100, "output_tokens": 20}, provider="codex")
        self.assertFalse(got["available"])
        self.assertIn("cache", got["reason"])
        self.assertIsNone(got["cached_input_tokens"])

    def test_negative_or_irreconcilable_values_are_unavailable(self):
        negative = spend.normalize_usage({
            "input_tokens": -1,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens": 1,
        }, provider="codex")
        self.assertFalse(negative["available"])
        impossible = spend.normalize_usage({
            "input_tokens": 10,
            "input_tokens_details": {"cached_tokens": 11},
            "output_tokens": 1,
        }, provider="codex")
        self.assertFalse(impossible["available"])


class TestTranscriptAccounting(unittest.TestCase):
    def test_nested_duplicate_messages_and_torn_tail(self):
        fd, path = tempfile.mkstemp()
        os.close(fd)
        row = {"provider": "codex", "message": {"id": "m1", "usage": {
            "input_tokens": 100,
            "input_tokens_details": {"cached_tokens": 40},
            "output_tokens": 20, "total_tokens": 120}}}
        with open(path, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(row) + "\n")
            stream.write(json.dumps(row) + "\n")
            stream.write('{"message":{"usage":')
        got = spend.read_provider_transcript(path, provider="codex")
        self.assertTrue(got["available"])
        self.assertEqual(got["messages"], 1)
        self.assertEqual(got["duplicates_removed"], 1)
        self.assertEqual(got["raw_total_tokens"], 120)
        self.assertEqual(got["effective_tokens"], 164)


if __name__ == "__main__":
    unittest.main()
