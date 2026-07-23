import os
from pathlib import Path

from tools._daemon import _pick_writable_home


def test_picks_first_writable(tmp_path):
    good = tmp_path / "a"
    result = _pick_writable_home([str(good), str(tmp_path / "b")])
    assert result == str(good)
    assert good.is_dir()


def test_skips_unwritable_and_picks_next(tmp_path):
    # /proc is a read-only pseudo-fs on Linux; mkdir under it raises OSError.
    unwritable = "/proc/nonexistent-mirage/state"
    good = tmp_path / "ok"
    result = _pick_writable_home([unwritable, str(good)])
    assert result == str(good)
    assert good.is_dir()


def test_all_unwritable_falls_back_to_home_default():
    result = _pick_writable_home(["/proc/x/y", "/sys/x/y"])
    assert result == str(Path.home() / ".mirage")


def test_import_populates_writable_mirage_home():
    # Importing tools._daemon must have pinned MIRAGE_HOME to a real dir.
    home = os.environ.get("MIRAGE_HOME")
    assert home
    assert os.path.isdir(home)
