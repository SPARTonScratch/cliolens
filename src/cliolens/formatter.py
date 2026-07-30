"""Output generator: renders the directory tree and assembles the final
markdown document (spec §5)."""

from __future__ import annotations

from datetime import datetime

from . import __version__
from .scanner import TRUNCATED_LINE_COUNT, FileEntry, ScanResult
from .utils import estimate_tokens, format_count, format_size

LANGUAGE_MAP = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".md": "markdown",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".html": "html",
    ".css": "css",
    ".sh": "bash",
    ".bash": "bash",
    ".ps1": "powershell",
    ".bat": "batch",
    ".cmd": "batch",
    ".rs": "rust",
    ".go": "go",
    ".java": "java",
    ".c": "c",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".cc": "cpp",
    ".h": "cpp",
    ".hpp": "cpp",
    ".rb": "ruby",
    ".php": "php",
    ".sql": "sql",
    ".toml": "toml",
    ".ini": "ini",
    ".cfg": "ini",
    ".xml": "xml",
}

# Comment marker used for truncation notices inside code fences.
_COMMENT_MARKERS = {
    "python": "#", "bash": "#", "yaml": "#", "toml": "#", "ini": "#",
    "sql": "--",
}
_DEFAULT_MARKER = "//"

_TOKENS_PH = "@@CLIOLENS_TOKENS@@"
_CHARS_PH = "@@CLIOLENS_CHARS@@"


def language_for(relative_path: str) -> str:
    """Infer a markdown fence language from the file extension."""
    name = relative_path.rsplit("/", 1)[-1].lower()
    dot = name.rfind(".")
    if dot <= 0:  # no extension, or dotfiles like '.gitignore'
        return ""
    return LANGUAGE_MAP.get(name[dot:], "")


class _Node:
    __slots__ = ("dirs", "files")

    def __init__(self) -> None:
        self.dirs: dict[str, _Node] = {}
        self.files: list[str] = []


class TreeRenderer:
    """ASCII tree with box-drawing characters.

    Rules (spec §5.2): forward slashes everywhere, directories before
    files at each level, both groups alphabetical, excluded items absent.
    """

    def render(self, root_name: str, directories: list[str], files: list[str]) -> str:
        root = _Node()
        for dir_path in directories:
            node = root
            for part in dir_path.split("/"):
                node = node.dirs.setdefault(part, _Node())
        for file_path in files:
            parts = file_path.split("/")
            node = root
            for part in parts[:-1]:
                node = node.dirs.setdefault(part, _Node())
            node.files.append(parts[-1])

        lines = [f"{root_name}/"]
        self._render(root, "", lines)
        return "\n".join(lines)

    def _render(self, node: _Node, prefix: str, lines: list[str]) -> None:
        items: list[tuple[str, bool]] = (
            [(name, True) for name in sorted(node.dirs, key=str.casefold)]
            + [(name, False) for name in sorted(node.files, key=str.casefold)]
        )
        for index, (name, is_dir) in enumerate(items):
            last = index == len(items) - 1
            connector = "└── " if last else "├── "
            lines.append(f"{prefix}{connector}{name}{'/' if is_dir else ''}")
            if is_dir:
                extension = "    " if last else "│   "
                self._render(node.dirs[name], prefix + extension, lines)


class MarkdownFormatter:
    """Assembles the single-file context dump."""

    def __init__(self, *, max_file_size_label: str) -> None:
        self._size_label = max_file_size_label

    def format(self, result: ScanResult) -> str:
        body = self._render_body(result)
        doc = self._render_header(result) + body
        # The header references the final document's own size; substitute
        # after measuring (the placeholder keeps the estimate honest).
        doc = doc.replace(_TOKENS_PH, format_count(estimate_tokens(doc)))
        doc = doc.replace(_CHARS_PH, format_count(len(doc)))
        return doc

    # -- sections -----------------------------------------------------------

    def _render_header(self, result: ScanResult) -> str:
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        root_name = result.root.name or str(result.root)
        return (
            "# Project Context Dump\n"
            "\n"
            "## Metadata\n"
            f"- **Project Name:** {root_name}\n"
            f"- **Root Path:** {result.root}\n"
            f"- **Generated At:** {now}\n"
            f"- **Files Scanned:** {format_count(result.files_scanned)}\n"
            f"- **Files Included:** {format_count(result.files_included)}\n"
            f"- **Files Skipped:** {format_count(result.files_skipped)}\n"
            f"- **Estimated Tokens:** {_TOKENS_PH} (~{_CHARS_PH} characters)\n"
            f"- **Total Size:** {format_size(result.total_bytes)}\n"
            "\n"
            "---\n"
            "\n"
        )

    def _render_body(self, result: ScanResult) -> str:
        root_name = result.root.name or str(result.root)
        tree = TreeRenderer().render(
            root_name, result.directories, [e.relative_path for e in result.entries]
        )
        parts = [
            f"## Directory Tree\n\n```\n{tree}\n```\n\n---\n\n## File Contents\n",
        ]
        parts.extend(self._render_entry(entry) for entry in result.entries)
        parts.append(f"End of dump. Generated by cliolens v{__version__}\n")
        return "\n".join(parts)

    def _render_entry(self, entry: FileEntry) -> str:
        header = f"### {entry.relative_path}\n"

        if entry.is_binary:
            return (
                f"{header}\n"
                f"[Binary file: {entry.mime_type}, {format_size(entry.size_bytes)}"
                " — content omitted]\n"
            )

        lang = language_for(entry.relative_path)
        fence = f"```{lang}" if lang else "```"
        body = entry.content or ""

        if entry.truncated:
            marker = _COMMENT_MARKERS.get(lang, _DEFAULT_MARKER)
            notice = (
                f"{marker} [Content truncated: file is {format_size(entry.size_bytes)}, "
                f"max allowed is {self._size_label}]\n"
                f"{marker} First {TRUNCATED_LINE_COUNT} lines shown below:\n"
            )
            if entry.omitted_lines > 0:
                tail = (
                    f"\n{marker} [... {format_count(entry.omitted_lines)}"
                    " more lines omitted ...]"
                )
            elif entry.head_cut:
                tail = f"\n{marker} [... remainder omitted ...]"
            else:
                shown = len(body.splitlines())
                tail = f"\n{marker} [all {shown} line(s) shown; file exceeds the size limit]"
            return f"{header}\n{fence}\n{notice}{body}{tail}\n```\n"

        return f"{header}\n{fence}\n{body}\n```\n"
