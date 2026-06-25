import unittest

import site_monitor


class SourceHealthPayloadTests(unittest.TestCase):
    def test_runtime_reason_is_used_when_result_is_unknown(self):
        site = {
            "notes": "[bot_diagnostic] result=sitemap_empty; method_attempted=sitemap",
            "_read_failure_reason": "fallback_empty",
            "_read_method": "fallback",
            "_method_attempted": ["selector", "fallback"],
            "_fallback_used": True,
        }
        payload, reason, _ = site_monitor.build_source_health_payload(
            site,
            {"reason": "unknown", "candidates": 0, "sent": 0},
            "2026-06-25T20:00:00+04:00",
        )

        self.assertEqual(reason, "fallback_empty")
        self.assertEqual(payload["last_result"], "fallback_empty")
        self.assertIsNone(payload["last_error"])
        self.assertEqual(payload["consecutive_fail_count"], 0)
        self.assertIn("method_attempted=selector,fallback", payload["notes"])
        self.assertIn("fallback_used=true", payload["notes"])
        self.assertNotIn("result=sitemap_empty", payload["notes"])

    def test_hard_failure_sets_last_error(self):
        site = {
            "_read_failure_reason": "selector_empty",
            "_read_method": "selector",
            "_method_attempted": ["selector"],
        }
        payload, reason, hard_fail_reasons = site_monitor.build_source_health_payload(
            site,
            {"reason": "selector_empty", "candidates": 0, "sent": 0},
            "2026-06-25T20:00:00+04:00",
        )

        self.assertIn(reason, hard_fail_reasons)
        self.assertEqual(payload["last_result"], "selector_empty")
        self.assertEqual(payload["last_error"], "selector_empty")
        self.assertNotIn("last_success_at", payload)

    def test_candidates_mark_article_found(self):
        site = {
            "_read_method": "rss",
            "_method_attempted": ["rss"],
            "_method_succeeded": "rss",
        }
        payload, reason, _ = site_monitor.build_source_health_payload(
            site,
            {"reason": "old_news", "candidates": 3, "sent": 0},
            "2026-06-25T20:00:00+04:00",
        )

        self.assertEqual(reason, "old_news")
        self.assertEqual(payload["last_success_at"], "2026-06-25T20:00:00+04:00")
        self.assertEqual(payload["last_article_found_at"], "2026-06-25T20:00:00+04:00")
        self.assertIsNone(payload["last_error"])


if __name__ == "__main__":
    unittest.main()
