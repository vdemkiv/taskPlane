"""R-0009 DoR discovery, classification, routing, and criterion ledger."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import evidence  # noqa: E402
import review_dor  # noqa: E402


class ReviewDorTest(unittest.TestCase):
    def source(self, kind, content="", **overrides):
        row = {
            "kind": kind,
            "identity": f"{kind}:fixture",
            "revision": "rev-1",
            "content": content,
            "accessible": True,
            "fresh": True,
        }
        row.update(overrides)
        return row

    def test_every_dor_source_is_probed_with_provenance_and_gap_status(self):
        rows = [
            self.source("pr_title", "Refactor event timeline"),
            self.source("pr_body", "Add AnalyticsSummary."),
            self.source("pr_comments", "Please check usability."),
            self.source("commits", "Extract Timeline component."),
            self.source("changelog", "Added response caching."),
            self.source("linked_issue", "The timeline must paginate."),
            self.source("linked_spec", "Copy action reports failure."),
            self.source("repository_contracts", "contract:event-api/v1"),
        ]
        ledger = review_dor.discover(rows, target_revision="target-1")

        self.assertEqual(set(ledger["source_checks"]),
                         set(review_dor.SOURCE_KINDS))
        for check in ledger["source_checks"].values():
            self.assertEqual(check["status"], "available")
            self.assertTrue(check["identity"])
            self.assertEqual(check["revision"], "rev-1")
            self.assertTrue(check["provenance_ref"])
        self.assertEqual(ledger["target_revision"], "target-1")

        gaps = review_dor.discover([
            self.source("pr_title", accessible=False),
            self.source("pr_body", fresh=False),
            self.source("pr_comments", contradictions=["pr_body:fixture"]),
        ])
        self.assertEqual(gaps["source_checks"]["pr_title"]["status"],
                         "inaccessible")
        self.assertEqual(gaps["source_checks"]["pr_body"]["status"], "stale")
        self.assertEqual(gaps["source_checks"]["pr_comments"]["status"],
                         "contradictory")
        self.assertEqual(gaps["source_checks"]["commits"]["status"], "missing")

    def test_classifier_handles_forms_and_only_clarifies_material_ambiguity(self):
        rows = [
            self.source("pr_title", "Refactor: extract timeline components"),
            self.source("pr_body", "- Add server-side response caching\n"
                        "- The build must not push changes"),
            self.source("commits", "Add event detail endpoint"),
            self.source("changelog", "Added analytics formatters"),
            self.source("pr_comments", "Please review security vulnerabilities"),
            self.source("linked_spec", "The delete endpoint should require authorization"),
            self.source("repository_contracts", "Event API contract v1"),
        ]
        ledger = review_dor.discover(rows)
        classes = {item["classification"] for item in ledger["items"]}
        self.assertEqual(classes, {
            "objective", "acceptance-criterion", "review-directive",
            "constraint", "context",
        })
        self.assertEqual(ledger["clarification_count"], 0)

        ambiguous = review_dor.discover([
            self.source("pr_body", "Maybe deletion can be public or authenticated",
                        material_ambiguity=True)
        ])
        self.assertEqual(ambiguous["clarification_count"], 1)
        self.assertFalse(ambiguous["approvable"])

        incidental = review_dor.discover([
            self.source("pr_body", "Maybe rename the local helper",
                        ambiguous=True)
        ])
        self.assertEqual(incidental["clarification_count"], 0)

    def test_criterion_ledger_has_four_states_and_blocks_unproven_or_bad_na(self):
        criteria = [
            {"id": "ac-1", "text": "Delete requires authorization"},
            {"id": "ac-2", "text": "Timeline paginates"},
            {"id": "ac-3", "text": "Legacy browser support"},
            {"id": "ac-4", "text": "Copy reports success"},
        ]
        rows = [
            review_dor.criterion_result(criteria[0], "pass", "tested",
                                        "artifact:test-1", "pytest", "security"),
            review_dor.criterion_result(criteria[1], "fail", "wrong page",
                                        "finding:f-1", "lens review", "frontend"),
            review_dor.criterion_result(criteria[2], "not-applicable", "", "",
                                        "contract check", "product"),
            review_dor.criterion_result(criteria[3], "unproven", "not run", "",
                                        "dynamic validation", "qa"),
        ]
        ledger = review_dor.criterion_ledger(rows, revision="review-r1")
        self.assertEqual({r["status"] for r in ledger["criteria"]},
                         {"pass", "fail", "unproven", "not-applicable"})
        self.assertFalse(ledger["approvable"])
        self.assertIn("unjustified_not_applicable", ledger["blockers"])
        for row in ledger["criteria"]:
            self.assertEqual(row["revision"], "review-r1")
            self.assertIn("verification_method", row)
            self.assertIn("responsible", row)

        good_na = review_dor.criterion_result(
            criteria[2], "not-applicable", "Target excludes browsers",
            "contract:web-targets", "contract check", "product")
        passing = review_dor.criterion_ledger([rows[0], good_na], revision="r2")
        self.assertTrue(passing["approvable"])

    def test_directives_route_independently_and_preserve_dynamic_validation(self):
        text = (
            "Please review the codebase and identify any issues you find. Consider:\n"
            "- authorization and injection risks\n"
            "- bugs and logic defects\n"
            "- confusing usability and interactions\n"
            "- performance under heavy load\n"
            "- code quality and maintainability\n"
            "- architecture and system design trade-offs\n"
            "Run the build too."
        )
        ledger = review_dor.discover([self.source("pr_comments", text)])
        routes = ledger["requested_lenses"]
        for lens in ("security", "qa", "design", "scalability",
                     "code-quality", "architecture"):
            self.assertIn(lens, routes)
        self.assertTrue(ledger["executable_validation_requested"])
        self.assertFalse(set(routes) & {row["id"] for row in ledger["criteria"]})

    def test_evidence_projection_exposes_criterion_obligations_without_judgment(self):
        dor = review_dor.discover([
            self.source("pr_body", "- Add AnalyticsSummary\n- Add event detail endpoint")
        ], target_revision="target-2")
        projected = evidence.review_dor_evidence(dor)
        self.assertEqual(projected["schema"],
                         "taskplane.review-dor-evidence-projection/v1")
        self.assertEqual(projected["target_revision"], "target-2")
        self.assertEqual(len(projected["criteria"]), 2)
        self.assertTrue(all(row["status"] == "" for row in projected["criteria"]))
        self.assertTrue(all(row["evidence_ref"] == ""
                            for row in projected["criteria"]))


if __name__ == "__main__":
    unittest.main()
