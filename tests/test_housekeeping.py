import os
import time

from pixkeep.housekeeping import sweep


def _touch(path, age):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").close()
    t = time.time() - age
    os.utime(path, (t, t))
    os.utime(os.path.dirname(path), (t, t))


def test_sweep_removes_only_stale_unkept(tmp_path):
    root = tmp_path / "tmp"
    _touch(str(root / "1" / "old" / "a.jpg"), 7 * 3600)
    _touch(str(root / "1" / "fresh" / "b.jpg"), 60)
    _touch(str(root / "1" / "retry" / "c.jpg"), 7 * 3600)
    _touch(str(root / "2" / "old2" / "d.jpg"), 7 * 3600)
    _touch(str(root / "comment_x.txt"), 7 * 3600)
    removed = sweep(str(root), 6 * 3600, keep={str(root / "1" / "retry")})
    assert removed == 3
    assert sorted(os.listdir(root)) == ["1"]                       # chat 2 emptied and removed
    assert sorted(os.listdir(root / "1")) == ["fresh", "retry"]


def test_sweep_missing_dir(tmp_path):
    assert sweep(str(tmp_path / "nope")) == 0
