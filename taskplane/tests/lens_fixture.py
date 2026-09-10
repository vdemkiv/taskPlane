"""Explicit detector fixture inputs, independent of interpreter caches."""
import os

def tree_files(root):
    files = []
    for directory, children, names in os.walk(root):
        children[:] = sorted(name for name in children if name != "__pycache__")
        files.extend(os.path.relpath(os.path.join(directory, name), root).replace(os.sep, "/")
                     for name in sorted(names) if not name.endswith((".pyc", ".pyo")))
    return files
