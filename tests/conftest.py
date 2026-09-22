"""Shared fixtures for the music-tui test suite.

bin/*.py scripts are hyphenated, so they can't be `import`ed by name -
`import_bin()` loads them the same way this session's ad hoc testing did,
via importlib.util.spec_from_file_location.
"""
import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest

BIN = Path(__file__).resolve().parent.parent / "bin"
_FIXTURE_DIR = Path(__file__).resolve().parent / "_fixture_audio"


def import_bin(name):
    """Load bin/<name> (e.g. "playlist-sync.py") as an importable module."""
    path = BIN / name
    modname = name.replace("-", "_").replace(".py", "")
    spec = importlib.util.spec_from_file_location(modname, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_silent(path, codec):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "anullsrc=r=8000:cl=mono", "-t", "1", "-c:a", codec, str(path)],
        check=True,
    )


@pytest.fixture(scope="session")
def _mp3_master():
    p = _FIXTURE_DIR / "master.mp3"
    if not p.exists():
        _make_silent(p, "libmp3lame")
    return p


@pytest.fixture(scope="session")
def _m4a_master():
    p = _FIXTURE_DIR / "master.m4a"
    if not p.exists():
        _make_silent(p, "aac")
    return p


@pytest.fixture(scope="session")
def _flac_master():
    p = _FIXTURE_DIR / "master.flac"
    if not p.exists():
        _make_silent(p, "flac")
    return p


@pytest.fixture
def tiny_mp3(tmp_path, _mp3_master):
    """A fresh, per-test copy of a 1-second silent mp3 - safe to mutate."""
    dest = tmp_path / "track.mp3"
    shutil.copy(_mp3_master, dest)
    return dest


@pytest.fixture
def tiny_m4a(tmp_path, _m4a_master):
    dest = tmp_path / "track.m4a"
    shutil.copy(_m4a_master, dest)
    return dest


@pytest.fixture
def tiny_flac(tmp_path, _flac_master):
    dest = tmp_path / "track.flac"
    shutil.copy(_flac_master, dest)
    return dest


_MASTER_BY_EXT = {".mp3": "_mp3_master", ".m4a": "_m4a_master", ".flac": "_flac_master"}


@pytest.fixture
def build_tree(tmp_path, _mp3_master, _m4a_master, _flac_master):
    """build_tree(spec) -> Path. spec: {"relative/path.ext": None} places a
    fresh copy of the matching tiny audio fixture there (ext picked from the
    path); no tagging - callers tag via read_tag/write_tag afterwards."""
    masters = {".mp3": _mp3_master, ".m4a": _m4a_master, ".flac": _flac_master}

    def _build(spec, root_name="root"):
        root = tmp_path / root_name
        root.mkdir(exist_ok=True)
        for rel in spec:
            dest = root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(masters[dest.suffix.lower()], dest)
        return root

    return _build
