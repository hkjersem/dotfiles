"""Conservative, explicit cleanup of recognised artifacts at conventional cache paths."""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import stat


SUPPORTED = ("bun", "npm", "pip", "go-build")
PACKAGE = re.compile(r"([a-zA-Z0-9._-]+)@(\d+\.\d+\.\d+[^/]*)")


@dataclass
class OrphanPlan:
    name: str
    root: Path
    identity: tuple
    entries: dict
    size: int
    preserved: tuple


def identity(info):
    return info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def conventional_path(name):
    return Path.home() / {
        "bun": ".bun/install/cache", "npm": ".npm",
        "pip": "Library/Caches/pip", "go-build": "Library/Caches/go-build",
    }[name]


def bun_entry(path, root, scope=""):
    info = path.lstat()
    if stat.S_ISREG(info.st_mode):
        return not scope and re.fullmatch(r"[0-9a-f]{16}\.npm", path.name) is not None
    if not stat.S_ISDIR(info.st_mode):
        return False
    if not scope and re.fullmatch(r"@[a-zA-Z0-9._-]+", path.name):
        return all(bun_entry(child, root, path.name + "/") for child in path.iterdir())
    package = PACKAGE.fullmatch(path.name)
    if package:
        manifest = path / "package.json"
        if manifest.is_symlink() or not manifest.is_file():
            return False
        try:
            data = json.loads(manifest.read_text())
        except (ValueError, UnicodeError) as exc:
            raise OSError(f"invalid Bun package manifest: {manifest.name}") from exc
        version = package[2].split("@@@", 1)[0]
        return isinstance(data, dict) and data.get("name") == scope + package[1] and data.get("version") == version
    if not re.fullmatch(r"[a-zA-Z0-9._-]+", path.name):
        return False
    # Bun keeps version aliases beside the unpacked packages. Never follow or
    # remove their targets through a link; only accept aliases into this cache.
    for alias in path.iterdir():
        if not re.fullmatch(r"\d+\.\d+\.\d+[^/]*", alias.name) or not alias.is_symlink():
            return False
        target = Path(os.path.abspath(alias.parent / os.readlink(alias)))
        expected = root / scope / (path.name + "@" + alias.name)
        if target != expected:
            return False
    return True


def recognised(name, path, root):
    info = path.lstat()
    if name == "bun":
        return bun_entry(path, root)
    if stat.S_ISLNK(info.st_mode):
        return False
    if name == "npm":
        return path.name == "_cacache" and stat.S_ISDIR(info.st_mode) and all(
            re.fullmatch(r"(?:content|index)-v\d+|tmp", child.name) and child.is_dir()
            and not child.is_symlink() for child in path.iterdir())
    if name == "pip":
        return path.name in ("http", "http-v2", "wheels") and stat.S_ISDIR(info.st_mode)
    if re.fullmatch(r"[0-9a-f]{2}", path.name) and stat.S_ISDIR(info.st_mode):
        return all(re.fullmatch(path.name + r"[0-9a-f]{62}-[ad]", child.name)
                   and not child.is_symlink() for child in path.iterdir())
    return path.name == "log.txt" and stat.S_ISREG(info.st_mode)


def snapshot(parent_fd, name, entries, relative):
    info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)):
        raise OSError(f"unsupported cache entry: {relative}")
    entries[relative] = identity(info)
    size = info.st_blocks * 512 if stat.S_ISREG(info.st_mode) else 0
    if stat.S_ISDIR(info.st_mode):
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
        try:
            if identity(os.fstat(fd)) != entries[relative]:
                raise OSError(f"cache changed during inspection: {relative}")
            for child in sorted(os.listdir(fd)):
                size += snapshot(fd, child, entries, relative / child)
            if identity(os.fstat(fd)) != entries[relative]:
                raise OSError(f"cache changed during inspection: {relative}")
        finally:
            os.close(fd)
    return size


def plan(name, validate):
    root = validate(name, str(conventional_path(name)))
    if not root.exists():
        return None
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    before = identity(os.fstat(fd))
    entries, preserved, size = {}, [], 0
    try:
        for child in sorted(root.iterdir()):
            if recognised(name, child, root):
                size += snapshot(fd, child.name, entries, Path(child.name))
            else:
                preserved.append(child.name)
        if identity(os.fstat(fd)) != before or identity(root.lstat()) != before:
            raise OSError("orphan cache changed during inspection")
    finally:
        os.close(fd)
    return OrphanPlan(name, root, before, entries, size, tuple(preserved))


def remove(plan):
    """Anchor removal to opened directories; never follow cached symlinks."""
    root_fd = os.open(plan.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if identity(os.fstat(root_fd)) != plan.identity:
            raise OSError("orphan cache changed after preview")

        def delete(parent_fd, relative):
            name = relative.name
            info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if relative not in plan.entries:
                raise OSError(f"new orphan entry appeared during cleanup: {relative}")
            if identity(info) != plan.entries[relative]:
                raise OSError(f"orphan entry changed after preview: {relative}")
            if stat.S_ISDIR(info.st_mode):
                fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
                try:
                    if identity(os.fstat(fd)) != plan.entries[relative]:
                        raise OSError(f"orphan directory changed after preview: {relative}")
                    for child in sorted(os.listdir(fd)):
                        delete(fd, relative / child)
                    current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                    if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
                        raise OSError(f"orphan directory replaced during cleanup: {relative}")
                    os.rmdir(name, dir_fd=parent_fd)
                finally:
                    os.close(fd)
            else:
                os.unlink(name, dir_fd=parent_fd)

        for relative in plan.entries:
            if relative.parent == Path("."):
                delete(root_fd, relative)
    finally:
        os.close(root_fd)
