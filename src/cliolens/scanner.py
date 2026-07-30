"""Discovery engine.

Walks the project tree, applies the filter pipeline (built-in rules →
.gitignore → user ``--exclude`` patterns), detects binary files, and
collects file metadata plus content.
"""

from __future__ import annotations

import fnmatch
import mimetypes
import os
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from pathspec import GitIgnoreSpec

from .utils import style

# --------------------------------------------------------------------------
# Built-in exclusions — ALWAYS active (independent of .gitignore handling).
# --------------------------------------------------------------------------

EXCLUDED_DIR_NAMES = frozenset(
    {
        ".git", ".svn", ".hg", "node_modules", "vendor", "__pycache__",
        ".mypy_cache", ".pytest_cache", ".ruff_cache", ".venv", "venv",
        "env", "ENV", "dist", "build", "target", ".out", ".idea", ".vscode",
    }
)
EXCLUDED_FILE_NAMES = frozenset(
    {
        ".DS_Store",
        "Thumbs.db",
        # Dependency lockfiles: large, machine-generated, low value for AI
        # context. The *.lock glob below catches the rest; these are the
        # common ones that don't end in .lock.
        "package-lock.json",
        "npm-shrinkwrap.json",
        "pnpm-lock.yaml",
        "go.sum",
        "uv.lock",
        "poetry.lock",
        "pipfile.lock",
        "yarn.lock",
        "composer.lock",
        "gemfile.lock",
        "cargo.lock",
        "mix.lock",
        "paket.lock",
    }
)
EXCLUDED_FILE_GLOBS = (
    "*.pyc", "*.pyo", "*.class", "*.o", "*.so",
    "*.dll", "*.exe", "*.bin", "*.lock",
)

# --------------------------------------------------------------------------
# Binary extension blacklist — fast-path classification (no file read).
# Case-insensitive on ALL platforms: a .PNG is a PNG everywhere.
# Content sniffing (BinaryDetector.sniff) remains the authority for
# extensions not listed here.
# --------------------------------------------------------------------------

BINARY_EXTENSIONS = frozenset(
    {
        # Images
        ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".tiff", ".tif",
        ".webp", ".avif", ".heic", ".heif", ".psd", ".xcf",
        # Audio
        ".mp3", ".wav", ".flac", ".aac", ".ogg", ".wma", ".m4a", ".opus",
        ".mid", ".midi",
        # Video
        ".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv", ".webm", ".m4v",
        ".mpg", ".mpeg", ".3gp",
        # Fonts
        ".woff", ".woff2", ".ttf", ".otf", ".eot",
        # Archives
        ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar", ".zst",
        ".lz", ".lz4", ".cab",
        # Documents
        ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
        ".odt", ".ods", ".odp",
        # Compiled / VM
        ".pyd", ".wasm", ".jar", ".war", ".ear", ".nupkg",
        # Disk / Firmware
        ".iso", ".img", ".dmg", ".vhd", ".vhdx",
        # Database
        ".sqlite", ".sqlite3", ".db", ".mdb", ".accdb",
        # ML / Scientific
        ".onnx", ".pb", ".h5", ".hdf5", ".npy", ".npz", ".pkl", ".pickle",
        ".parquet", ".feather",
        # 3D / Misc
        ".gltf", ".glb", ".fbx", ".obj", ".stl", ".blend", ".swf", ".dat",
    }
)

_BINARY_SNIFF_BYTES = 8192          # spec §6.4: inspect the first 8KB
_CONTROL_CHAR_BUDGET = 0.30         # spec §6.4: >30% control chars → binary
TRUNCATED_LINE_COUNT = 50           # spec §5.4: keep the first 50 lines
_HEAD_SAMPLE_BYTES = 256 * 1024     # max bytes buffered when truncating

IS_WINDOWS = os.name == "nt"

WarnFn = Callable[[str], None]


def _fold(name: str) -> str:
    """Case-fold for comparisons on case-insensitive filesystems (Windows)."""
    return name.lower() if IS_WINDOWS else name


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------


@dataclass
class FileEntry:
    """One file that made it into the dump."""

    relative_path: str              # forward-slash path relative to the root
    absolute_path: Path
    size_bytes: int
    is_binary: bool = False
    content: str | None = None      # None for binary entries
    truncated: bool = False         # content holds only the first N lines
    omitted_lines: int = 0
    head_cut: bool = False          # the raw sample itself was cut mid-file
    mime_type: str = "text/plain"


@dataclass
class SkipRecord:
    """One path that was deliberately left out, with the reason why."""

    relative_path: str
    reason: str


@dataclass
class ScanResult:
    """Everything the discovery engine produced."""

    root: Path
    entries: list[FileEntry] = field(default_factory=list)
    directories: list[str] = field(default_factory=list)
    skipped: list[SkipRecord] = field(default_factory=list)
    files_scanned: int = 0
    total_bytes: int = 0

    @property
    def files_included(self) -> int:
        return len(self.entries)

    @property
    def files_skipped(self) -> int:
        return len(self.skipped)


# --------------------------------------------------------------------------
# Filter pipeline (chain of responsibility: built-in → gitignore → user)
# --------------------------------------------------------------------------


class BuiltInFilter:
    """Hard-coded exclusions that are always active."""

    def __init__(self) -> None:
        self._dirs = {_fold(n) for n in EXCLUDED_DIR_NAMES}
        self._files = {_fold(n) for n in EXCLUDED_FILE_NAMES}
        self._globs = tuple(_fold(g) for g in EXCLUDED_FILE_GLOBS)

    def reason(self, name: str, is_dir: bool) -> str | None:
        folded = _fold(name)
        if is_dir:
            if folded in self._dirs:
                return f"built-in: {name}/"
            return None
        if folded in self._files:
            return f"built-in: {name}"
        for glob in self._globs:
            if fnmatch.fnmatch(folded, glob):
                return f"built-in: {glob}"
        return None


class GitignoreFilter:
    """Stack of .gitignore specs, one per directory that contains one.

    Each spec is queried against paths relative to its own directory,
    matching git semantics. Composition across levels follows git's
    last-match-wins rule, with one guard: pathspec 1.x reports ``False``
    for every path a negation-containing spec doesn't positively match,
    so a ``False`` verdict is trusted only when a negation pattern
    actually matches the path (checked against a cached negations-only
    twin spec).
    """

    def __init__(self, on_warning: WarnFn | None = None) -> None:
        # (prefix, full spec, negations-as-positives twin or None)
        self._specs: list[tuple[str, GitIgnoreSpec, GitIgnoreSpec | None]] = []
        self._warn = on_warning or (lambda _msg: None)

    def load_dir(self, dir_path: Path, rel_prefix: str) -> None:
        """Load the .gitignore living in *dir_path*, if any."""
        gitignore = dir_path / ".gitignore"
        if not gitignore.is_file():
            return
        try:
            raw = gitignore.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            self._warn(f"could not read {gitignore}: {exc}")
            return

        # Validate line-by-line via the public API: parse what we can,
        # skip (and report) malformed lines.
        valid_lines: list[str] = []
        malformed = 0
        for line in raw.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            try:
                GitIgnoreSpec.from_lines([line])
                valid_lines.append(line)
            except Exception:
                malformed += 1
        if malformed:
            self._warn(f"{gitignore}: skipped {malformed} malformed pattern(s)")
        if not valid_lines:
            return

        try:
            spec = GitIgnoreSpec.from_lines(valid_lines)
        except Exception as exc:  # defensive: a bad .gitignore never kills the run
            self._warn(f"{gitignore}: could not parse ({exc})")
            return

        neg_lines = [ln[1:] for ln in valid_lines if ln.startswith("!") and ln[1:].strip()]
        neg_spec: GitIgnoreSpec | None = None
        if neg_lines:
            try:
                neg_spec = GitIgnoreSpec.from_lines(neg_lines)
            except Exception:
                neg_spec = None

        self._specs.append((rel_prefix, spec, neg_spec))

    def verdict(self, rel_posix: str, is_dir: bool) -> bool | None:
        """``True`` → excluded, ``False`` → explicitly re-included,
        ``None`` → no rule matched at any level."""
        target = rel_posix + "/" if is_dir else rel_posix
        result: bool | None = None
        for prefix, spec, neg_spec in self._specs:
            if prefix:
                if not (rel_posix == prefix or rel_posix.startswith(prefix + "/")):
                    continue
                sub = target[len(prefix) + 1 :]
            else:
                sub = target
            if not sub:
                continue
            matched = spec.match_file(sub)
            if matched is True:
                result = True
            elif matched is False and neg_spec is not None and neg_spec.match_file(sub):
                # A negation genuinely matched this path — trust it.
                result = False
            # matched is None, or False without a matching negation
            # (pathspec 1.x quirk): this level has no real opinion.
        return result


class UserExcludeFilter:
    """Glob patterns supplied via ``--exclude`` (applied last, so they win)."""

    def __init__(self, patterns: Iterable[str]) -> None:
        self._patterns = [p for p in patterns if p]

    def reason(self, rel_posix: str, is_dir: bool) -> str | None:
        for pattern in self._patterns:
            if self._matches(pattern, rel_posix, is_dir):
                return f"--exclude: {pattern}"
        return None

    @staticmethod
    def _matches(pattern: str, rel_posix: str, is_dir: bool) -> bool:
        raw = pattern.replace("\\", "/")
        dir_only = raw.endswith("/")
        pat = raw.rstrip("/")
        if dir_only and not is_dir:
            # 'docs/' prunes the directory itself; children never get here.
            return False
        if IS_WINDOWS:
            pat, rel_posix = pat.lower(), rel_posix.lower()
        # 1. Full relative-path match:  'src/gen/*.py'
        if fnmatch.fnmatch(rel_posix, pat):
            return True
        # 2. Basename match (gitignore-style):  '*.test.js'
        if "/" not in pat and fnmatch.fnmatch(rel_posix.rsplit("/", 1)[-1], pat):
            return True
        # 3. Path-suffix match:  'utils/helpers.py' hits 'src/utils/helpers.py'
        if "/" in pat:
            pat_parts = pat.split("/")
            rel_parts = rel_posix.split("/")
            if len(rel_parts) >= len(pat_parts) and fnmatch.fnmatch(
                "/".join(rel_parts[-len(pat_parts) :]), pat
            ):
                return True
        return False


class FilterPipeline:
    """Chain of responsibility: built-in → .gitignore → user exclusions."""

    def __init__(
        self,
        *,
        use_gitignore: bool,
        user_patterns: Iterable[str],
        on_warning: WarnFn | None = None,
    ) -> None:
        self._built_in = BuiltInFilter()
        self._gitignore = GitignoreFilter(on_warning=on_warning) if use_gitignore else None
        self._user = UserExcludeFilter(user_patterns)

    @property
    def gitignore(self) -> GitignoreFilter | None:
        return self._gitignore

    def exclusion_reason(self, name: str, rel_posix: str, is_dir: bool) -> str | None:
        reason = self._built_in.reason(name, is_dir)
        if reason:
            return reason
        if self._gitignore is not None and self._gitignore.verdict(rel_posix, is_dir) is True:
            return ".gitignore"
        return self._user.reason(rel_posix, is_dir)


# --------------------------------------------------------------------------
# Binary detection
# --------------------------------------------------------------------------


class BinaryDetector:
    """Heuristic binary detection per spec §6.4."""

    @staticmethod
    def sniff(path: Path) -> tuple[bool, str]:
        """Return ``(is_binary, best_effort_encoding)`` from the first 8KB."""
        with open(path, "rb") as fh:
            raw = fh.read(_BINARY_SNIFF_BYTES)
        if b"\x00" in raw:
            return True, "utf-8"
        try:
            text = raw.decode("utf-8")
            encoding = "utf-8"
        except UnicodeDecodeError:
            text = raw.decode("latin-1")  # accepts every byte sequence
            encoding = "latin-1"
        if not text:
            return False, encoding
        control = sum(
            1 for ch in text if ch not in "\n\t\r" and unicodedata.category(ch) == "Cc"
        )
        return control / len(text) > _CONTROL_CHAR_BUDGET, encoding

    @staticmethod
    def classify(path: Path) -> tuple[bool, str]:
        """Classify a file as binary or text.

        Fast path: known binary extension → binary, no file read.
        Slow path: anything else → content-sniff the first 8KB.
        """
        if path.suffix.lower() in BINARY_EXTENSIONS:
            return True, "utf-8"
        return BinaryDetector.sniff(path)


# --------------------------------------------------------------------------
# Scanner
# --------------------------------------------------------------------------


def _default_warn(message: str) -> None:  # pragma: no cover - trivial
    import sys

    print(f"{style('warning:', 'yellow', 'bold')} {message}", file=sys.stderr)


class ProjectScanner:
    """Walks the tree, applies filters, reads content, collects metadata."""

    def __init__(
        self,
        root: Path,
        *,
        max_file_size: int,
        use_gitignore: bool = True,
        user_excludes: Iterable[str] | None = None,
        follow_symlinks: bool = False,
        protect_paths: Iterable[Path] | None = None,
        warn: WarnFn = _default_warn,
    ) -> None:
        self._root = root.resolve()
        self._max_file_size = max_file_size
        self._follow = follow_symlinks
        # Paths that must never be scanned (e.g. the output file living
        # inside the project — a re-run must not dump the previous dump).
        self._protect = {p.resolve() for p in (protect_paths or ())}
        self._warn = warn
        self._pipeline = FilterPipeline(
            use_gitignore=use_gitignore,
            user_patterns=user_excludes or (),
            on_warning=warn,
        )
        self._visited_dirs: set[tuple[int, int]] = set()

    # -- public API ---------------------------------------------------------

    def scan(self) -> ScanResult:
        result = ScanResult(root=self._root)
        if self._pipeline.gitignore is not None:
            self._pipeline.gitignore.load_dir(self._root, "")
        self._walk(self._root, "", result)
        result.directories.sort(key=str.casefold)
        result.entries.sort(key=lambda e: e.relative_path.casefold())
        return result

    # -- internals ----------------------------------------------------------

    def _walk(self, dir_path: Path, rel: str, result: ScanResult) -> None:
        if rel and self._pipeline.gitignore is not None:
            self._pipeline.gitignore.load_dir(dir_path, rel)

        try:
            with os.scandir(dir_path) as it:
                children = sorted(it, key=lambda e: e.name.casefold())
        except PermissionError:
            self._warn(f"permission denied: {dir_path} — directory skipped")
            return
        except OSError as exc:
            self._warn(f"could not read {dir_path}: {exc}")
            return

        for child in children:
            name = child.name
            rel_child = f"{rel}/{name}" if rel else name
            try:
                is_symlink = child.is_symlink()
                is_dir = child.is_dir(follow_symlinks=False)
            except OSError as exc:
                self._warn(f"could not stat {child.path}: {exc}")
                result.skipped.append(SkipRecord(rel_child, "stat failed"))
                continue

            if is_dir:
                self._handle_dir(child, rel_child, is_symlink, result)
            else:
                self._handle_file(child, rel_child, is_symlink, result)

    def _handle_dir(self, child, rel_child: str, is_symlink: bool, result: ScanResult) -> None:
        if is_symlink and not self._follow:
            result.skipped.append(
                SkipRecord(rel_child + "/", "symlinked directory (use --follow-symlinks)")
            )
            return

        reason = self._pipeline.exclusion_reason(child.name, rel_child, is_dir=True)
        if reason:
            result.skipped.append(SkipRecord(rel_child + "/", f"excluded ({reason})"))
            return

        if is_symlink:
            # Loop-safe traversal: never re-enter a directory inode we've seen.
            try:
                st = child.stat()
            except OSError as exc:
                self._warn(f"could not stat symlink {child.path}: {exc}")
                result.skipped.append(SkipRecord(rel_child + "/", "broken symlink"))
                return
            key = (st.st_dev, st.st_ino)
            if key in self._visited_dirs:
                result.skipped.append(SkipRecord(rel_child + "/", "symlink loop detected"))
                return
            self._visited_dirs.add(key)

        result.directories.append(rel_child)
        self._walk(Path(child.path), rel_child, result)

    def _handle_file(self, child, rel_child: str, is_symlink: bool, result: ScanResult) -> None:
        result.files_scanned += 1
        abs_path = Path(child.path)

        if is_symlink and not abs_path.exists():
            result.skipped.append(SkipRecord(rel_child, "broken symlink"))
            return

        if abs_path in self._protect:
            result.skipped.append(SkipRecord(rel_child, "output file (protected)"))
            return

        reason = self._pipeline.exclusion_reason(child.name, rel_child, is_dir=False)
        if reason:
            result.skipped.append(SkipRecord(rel_child, f"excluded ({reason})"))
            return

        try:
            size = child.stat().st_size
        except OSError as exc:
            self._warn(f"could not stat {abs_path}: {exc}")
            result.skipped.append(SkipRecord(rel_child, "stat failed"))
            return

        try:
            is_binary, encoding = BinaryDetector.classify(abs_path)
        except OSError as exc:
            self._warn(f"could not read {abs_path}: {exc} — file skipped")
            result.skipped.append(SkipRecord(rel_child, "unreadable (locked?)"))
            return

        if is_binary:
            mime, _ = mimetypes.guess_type(child.name)
            result.entries.append(
                FileEntry(
                    relative_path=rel_child,
                    absolute_path=abs_path,
                    size_bytes=size,
                    is_binary=True,
                    mime_type=mime or "application/octet-stream",
                )
            )
            result.total_bytes += size
            return

        if size > self._max_file_size:
            entry = self._read_truncated(abs_path, rel_child, size, encoding, result)
        else:
            entry = self._read_full(abs_path, rel_child, size, encoding, result)

        if entry is not None:
            result.entries.append(entry)
            result.total_bytes += size

    def _read_full(
        self, path: Path, rel: str, size: int, encoding: str, result: ScanResult
    ) -> FileEntry | None:
        try:
            with open(path, encoding=encoding, errors="replace") as fh:
                content = fh.read()
        except OSError as exc:
            self._warn(f"could not read {path}: {exc} — file skipped")
            result.skipped.append(SkipRecord(rel, "unreadable (locked?)"))
            return None
        return FileEntry(
            relative_path=rel, absolute_path=path, size_bytes=size, content=content
        )

    def _read_truncated(
        self, path: Path, rel: str, size: int, encoding: str, result: ScanResult
    ) -> FileEntry | None:
        """Read only what the truncation notice needs: the first
        ``TRUNCATED_LINE_COUNT`` lines plus a total line count, without
        ever holding the whole file in memory."""
        try:
            total_lines = 0
            head = b""
            with open(path, "rb") as fh:
                while chunk := fh.read(1 << 20):
                    total_lines += chunk.count(b"\n")
                    if len(head) < _HEAD_SAMPLE_BYTES:
                        head += chunk
            head_text = head[:_HEAD_SAMPLE_BYTES].decode(encoding, errors="replace")
        except OSError as exc:
            self._warn(f"could not read {path}: {exc} — file skipped")
            result.skipped.append(SkipRecord(rel, "unreadable (locked?)"))
            return None

        lines = head_text.splitlines()[:TRUNCATED_LINE_COUNT]
        omitted = max(total_lines - TRUNCATED_LINE_COUNT, 0)
        return FileEntry(
            relative_path=rel,
            absolute_path=path,
            size_bytes=size,
            content="\n".join(lines),
            truncated=True,
            omitted_lines=omitted,
            head_cut=size > len(head),
        )
