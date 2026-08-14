"""Keep this fixture out of the REPOSITORY's own pytest collection.

`tree-a/` and `tree-b/` are the two states of a synthetic pull request. They
are DATA — plain files a builder commits into a throwaway repo — and they
contain python test modules on purpose, because the diff the scenario grades
has to be shaped like the real thing: source, docs, and tests.

Bare `pytest` at the repo root walks every directory, so without this file it
collects `tree-a/tests/test_checkout.py` AND `tree-b/tests/test_checkout.py`.
Neither directory is a package, so both import as the module `test_checkout`
and pytest aborts the whole run with

    import file mismatch: ... unique basename for your test file modules

That is a repo-wide collection failure caused by a fixture, and CI would
never see it: the CI leg scopes pytest to `taskplane/tests`. It lands on a
developer running `pytest` at the root, which is the worst place for it.

`collect_ignore_glob` is the narrowest fix available — it turns off
collection for this subtree only, leaving the rest of the repo's collection
exactly as it was. The fixture's tests are still run: by the model, inside
the throwaway checkout, which is the only place they mean anything.
"""

collect_ignore_glob = ["*"]
