"""Capability requirements for tests that create a strict workspace binding."""
import os

import pytest


HAS_BINDING_RUNTIME = (os.open in os.supports_dir_fd
                       and hasattr(os, "O_DIRECTORY") and hasattr(os, "O_NOFOLLOW"))
REASON = "Strict workspace binding requires descriptor-relative no-follow directory operations"
requires_binding_runtime = pytest.mark.skipif(not HAS_BINDING_RUNTIME, reason=REASON)


def require_binding_runtime():
    if not HAS_BINDING_RUNTIME:
        pytest.skip(REASON)
