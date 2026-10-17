# Host compatibility observations

Record the actual host version, available native operations, loaded Taskplane member hashes and observed hook behavior. Distinguish a supported operation from a tool merely appearing in an inventory. Unsupported host authority must be reported and never bypassed.

Observe changes with supported read-only version and capability interfaces. A changed host or runtime invalidates affected native evidence; retain the previous result and recheck the affected functional boundary. Missing identity, cancellation, process exit or hook completion stays unknown. Native counters, delivered bytes and account allowance are separate measurements and cannot substitute for execution evidence.

The planned `scripts/check_host_updates.py` and its tests are owned by release verification and may not yet exist in a bootstrap package. Their eventual checks must report changed, unchanged, unavailable and failed observations accurately. A local fixture does not prove an external monitor ran.

Use the existing authorized monitor. Do not create a duplicate schedule, change notification preferences or publish a message without the applicable authorization. Capture a genuine future scheduled wake and any authorized meaningful-change notification with native schedule/run/message references. An ACTIVE schedule or immediate manual run is not a future wake.

Candidate status: final host compatibility, installed-runtime and scheduled-wake evidence remain separate delivery records. This document does not claim those external checks have passed.
