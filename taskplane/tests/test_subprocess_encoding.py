"""No subprocess may decode its child with the LOCALE's encoding.

This exists because CI went red on it. `subprocess.run(..., text=True)`
with no `encoding` decodes the child's bytes using
`locale.getpreferredencoding()`. On a developer machine that is UTF-8 and
everything passes; on a bare GitHub runner it is `ANSI_X3.4-1968` — plain
ascii — and the FIRST non-ASCII byte raises UnicodeDecodeError from
inside subprocess, before any assertion in the calling test runs.

That is a uniquely bad failure shape. It is invisible locally, it points
at CPython rather than at taskplane, and the error message says nothing
about what the test was checking. It is also latent everywhere rather
than local to one call: taskplane's own CLI reconfigures its streams to
UTF-8 and prints em dashes throughout, so every one of these call sites
is one message away from the same crash — the site that actually fired
was simply the first whose output happened to contain one.

So the rule is mechanical and checkable: if a call passes `text=True`
(or `universal_newlines=True`), it passes `encoding=` too.

Reproduce the original failure with:

    LC_ALL=C PYTHONUTF8=0 PYTHONCOERCECLOCALE=0 python3 -m pytest -q
"""
import ast
import io
import os
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
SKIP_DIRS = {
    ".git", ".tp-work", "__pycache__", "node_modules", ".venv", "venv",
    "_to_delete",
}
RUNNERS = {"run", "Popen", "check_output", "call", "check_call"}


def _decoding_calls_without_encoding(src):
    """[(lineno, runner)] for calls that decode using the ambient locale."""
    found = []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(
            fn, "id", "")
        if name not in RUNNERS:
            continue
        kwargs = {k.arg for k in node.keywords if k.arg}
        decodes = any(
            k.arg in ("text", "universal_newlines")
            and isinstance(k.value, ast.Constant) and k.value.value is True
            for k in node.keywords)
        if decodes and "encoding" not in kwargs:
            found.append((node.lineno, name))
    return found


def _python_files():
    for root, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fn in files:
            if fn.endswith(".py"):
                yield os.path.join(root, fn)


class TestNoSubprocessDecodesWithTheAmbientLocale(unittest.TestCase):

    def test_every_text_mode_subprocess_names_its_encoding(self):
        offenders = []
        for path in _python_files():
            with io.open(path, encoding="utf-8") as f:
                src = f.read()
            try:
                calls = _decoding_calls_without_encoding(src)
            except SyntaxError:
                continue          # not ours to compile
            for lineno, runner in calls:
                offenders.append(
                    f"{os.path.relpath(path, REPO)}:{lineno} "
                    f"subprocess.{runner}(text=True) with no encoding=")
        self.assertEqual(
            offenders, [],
            "these decode the child process with the LOCALE's encoding, "
            "which is ascii on a bare CI runner — add "
            'encoding="utf-8", errors="replace":\n  '
            + "\n  ".join(offenders))

    # --- the detector itself, or the test above is worth nothing ---

    def test_it_catches_the_exact_shape_that_broke_ci(self):
        src = ('import subprocess\n'
               'subprocess.run(["x"], capture_output=True, text=True)\n')
        self.assertEqual(_decoding_calls_without_encoding(src),
                         [(2, "run")])

    def test_it_catches_the_legacy_spelling_too(self):
        src = 'subprocess.Popen(["x"], universal_newlines=True)\n'
        self.assertEqual(_decoding_calls_without_encoding(src),
                         [(1, "Popen")])

    def test_a_call_that_names_its_encoding_is_not_flagged(self):
        src = ('subprocess.run(["x"], text=True, encoding="utf-8",\n'
               '               errors="replace")\n')
        self.assertEqual(_decoding_calls_without_encoding(src), [])

    def test_byte_mode_is_not_flagged(self):
        """No decoding happens, so no encoding is needed."""
        src = 'subprocess.run(["x"], capture_output=True)\n'
        self.assertEqual(_decoding_calls_without_encoding(src), [])

    def test_text_false_is_not_flagged(self):
        src = 'subprocess.run(["x"], text=False)\n'
        self.assertEqual(_decoding_calls_without_encoding(src), [])

    def test_it_looks_past_the_module_alias(self):
        """`from subprocess import run` is the same hazard."""
        src = 'run(["x"], text=True)\n'
        self.assertEqual(_decoding_calls_without_encoding(src), [(1, "run")])

    def test_it_actually_scanned_this_repository(self):
        """A walker that finds nothing would pass the headline test."""
        seen = [p for p in _python_files()
                if os.path.basename(p) == "depgraph.py"]
        self.assertTrue(seen, "the file walk found no taskplane sources")


if __name__ == "__main__":
    unittest.main()
