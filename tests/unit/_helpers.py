"""Shared test helpers (Python 3.7 stdlib only)."""
import contextlib
import os
import shutil
import subprocess
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPTS_DIR = os.path.join(_ROOT, "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

ROOT_DIR = _ROOT
FIXTURES_DIR = os.path.join(_ROOT, "tests", "fixtures")


def make_tree(root, spec):
    """Create files under root. spec maps relative path -> str or bytes."""
    for rel, content in spec.items():
        path = os.path.join(root, rel)
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        if isinstance(content, bytes):
            with open(path, "wb") as f:
                f.write(content)
        else:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)


@contextlib.contextmanager
def tempdir():
    d = os.path.realpath(tempfile.mkdtemp(prefix="skr-test-"))
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


def run_path(path, *args):
    return subprocess.run([sys.executable, path] + list(args), cwd=ROOT_DIR,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True)


def run_script(name, *args):
    return run_path(os.path.join(SCRIPTS_DIR, name), *args)
