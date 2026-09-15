# Test strategy — `taskplane.test-strategy/v1`

Design authors the strategy at the exact worker-owned output path. It records
planned behavioral checks; it does not claim that Build or tests have run.
The top-level fields are closed: use exactly the fields in the example below.

- `acceptance_criteria` is nonempty. Each row has a unique `id` and nonempty
  unique `selectors`. A selector belongs to exactly one criterion.
- Selectors use exact pytest node syntax, including for unittest methods:
  `test_feature.py::FeatureTests::test_behavior`. These are planned selectors;
  the implementation may use standard-library unittest and remain dependency-free.
- `producers` is nonempty, with unique `id`, repository `path`, and bounded
  `slice`. Each producer lists nonempty unique `consumers` and `freshness_inputs`.
- Each producer needs at least one `severed_edges` row with a named `consumer`,
  concrete executable mutation description, and exact `selector` that would
  fail if the consumer stopped using the producer's correct behavior. Do not
  claim mutation execution during Design.
- `interface_kind` is `in-process`, `serialized`, or `external`. In-process
  uses an empty `interface_fixtures` list and a real consumer journey. Other
  kinds require fixture rows with `path` and the same `slice` as their producer.
- Preserve the exact ordered `failure_policy` and `validation` values below.
  These are policy declarations, not evidence that CI or validation has occurred.
- Fingerprints are ordinary content hashes, not approval or runtime receipts.
  After authoring, hash each producer without `fingerprint_sha256`, then hash
  the complete strategy without `contract_fingerprint_sha256`. Use SHA-256 of
  UTF-8 JSON with sorted keys, separators `(',', ':')`, `ensure_ascii=True`,
  `allow_nan=False`, and no trailing newline. Changing any field requires new hashes.

## Complete minimal example

Adapt the paths, criteria, producer and mutation to the selected requirement;
then recompute both hashes. This example is structurally valid and is not a
claim of actual feature verification.

```json
{
  "schema": "taskplane.test-strategy/v1",
  "acceptance_criteria": [
    {
      "id": "AC-1",
      "selectors": [
        "test_feature.py::test_behavior"
      ]
    }
  ],
  "producers": [
    {
      "id": "feature",
      "path": "feature.py",
      "slice": "feature",
      "consumers": [
        "test_feature.py"
      ],
      "severed_edges": [
        {
          "consumer": "test_feature.py",
          "mutation": "Replace the producer result with an incorrect value; the selected test must fail.",
          "selector": "test_feature.py::test_behavior"
        }
      ],
      "interface_kind": "in-process",
      "interface_fixtures": [],
      "freshness_inputs": [
        "source",
        "tests",
        "settings"
      ],
      "fingerprint_sha256": "ec7c9b59471b062584d141fc303449a4df0e43f9bff7956e9e9a57e241dafd68"
    }
  ],
  "failure_policy": {
    "classes": [
      "product",
      "test",
      "infrastructure",
      "environment"
    ],
    "correction_requires": [
      "class",
      "reason",
      "owner",
      "cluster"
    ]
  },
  "validation": {
    "layers": [
      "static",
      "exact-selector",
      "changed-radius",
      "proportional-suite",
      "authoritative-ci"
    ],
    "fingerprint_inputs": [
      "source",
      "tests",
      "settings",
      "inventory",
      "selector",
      "radius",
      "shard-plan",
      "runner",
      "environment"
    ],
    "reuse_unchanged_green": "cite",
    "broad_local_default": "refuse",
    "authoritative_matrix_runs": 1
  },
  "contract_fingerprint_sha256": "442af4f27903c5a567e18c6a2f220aeff519ce148448d2dce508aac4563bd62e"
}
```

In `design/contract.json`, set `test_strategy` to
`{"path":"design/test-strategy.json"}` and set `test_strategy_reference` to
`{"schema":"taskplane.design-test-strategy-reference/v1","path":"design/test-strategy.json","strategy_fingerprint":"<the strategy's contract_fingerprint_sha256>"}`.
Use the actual declared path if it differs. Acceptance mappings must carry
nonempty `tests` or `selectors`, in addition to their human validation explanation.
