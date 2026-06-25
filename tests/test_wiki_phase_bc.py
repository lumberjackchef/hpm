"""Tests for wiki sync, lint, and contradiction awareness (Phases B & C)."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from hpm.wiki import lint as wiki_lint
from hpm.wiki import sync as wiki_sync


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_wiki():
    """Set up a temporary wiki with some sample pages."""
    with tempfile.TemporaryDirectory() as td:
        wiki_path = Path(td) / "wiki"
        wiki_path.mkdir()
        for subdir_name in ("entities", "concepts", "comparisons", "queries"):
            (wiki_path / subdir_name).mkdir()

        with mock.patch("hpm.config.WIKI_DIR", wiki_path):
            yield wiki_path


def _write_page(wiki_root: Path, page_type: str, slug: str, title: str,
                confidence: str = "high", contested: bool = False,
                tags: list[str] | None = None,
                contradictions: list[str] | None = None,
                body: str = "Body content.",
                wikilinks: list[str] | None = None,
                line_count: int | None = None) -> Path:
    """Create a wiki page for testing."""
    from hpm.wiki import types as wt

    frontmatter_parts = [
        "---",
        f"title: {title}",
        f"created: 2026-06-24",
        f"updated: 2026-06-24",
        f"type: {page_type}",
    ]
    if tags:
        frontmatter_parts.append(f"tags: [{' '.join(tags)}]")
    frontmatter_parts.append(f"confidence: {confidence}")
    frontmatter_parts.append(f"contested: {'true' if contested else 'false'}")
    if contradictions:
        frontmatter_parts.append(f"contradictions: [{' '.join(contradictions)}]")
    frontmatter_parts.append("---\n")

    if wikilinks:
        for link in wikilinks:
            frontmatter_parts.append(f"Related: [[{link}]]")

    if line_count:
        frontmatter_parts.append(body + "\n" + "\n".join(f"Line {i}" for i in range(line_count)))
    else:
        frontmatter_parts.append(body)

    subdir = wt.subdir_for(page_type)
    subdir.mkdir(parents=True, exist_ok=True)
    path = subdir / f"{slug}.md"
    path.write_text("\n".join(frontmatter_parts))
    return path


def _write_index(wiki_root: Path, pages: list[tuple[str, str, str]]) -> None:
    """Write an index.md with the given page entries.

    Each entry: (type, slug, title)
    """
    lines = ["# Wiki Index\n"]
    by_type: dict[str, list[tuple[str, str]]] = {}
    for page_type, slug, title in pages:
        by_type.setdefault(page_type, []).append((slug, title))

    for page_type, entries in by_type.items():
        heading = page_type[0].upper() + page_type[1:] + "s"
        lines.append(f"## {heading}\n")
        for slug, title in entries:
            lines.append(f"- [{title}]({page_type}s/{slug}.md)\n")
        lines.append("")

    (wiki_root / "index.md").write_text("".join(lines))


# ── Wiki Lint ───────────────────────────────────────────────────────────────


class TestWikiLint:
    def test_no_wiki_dir(self):
        with mock.patch("hpm.config.WIKI_DIR", Path("/nonexistent/hpm/wiki")):
            issues = wiki_lint.cmd_lint()
            assert any("error" in i["severity"] for i in issues)

    def test_no_pages(self, tmp_wiki):
        issues = wiki_lint.cmd_lint()
        assert any("info" in i["severity"] for i in issues)

    def test_missing_frontmatter(self, tmp_wiki):
        _write_page(tmp_wiki, "concept", "no-fm", "No FM", confidence="")
        issues = wiki_lint.cmd_lint()
        frontmatter_issues = [i for i in issues if "Missing frontmatter" in i["message"]]
        assert len(frontmatter_issues) >= 1

    def test_missing_from_index(self, tmp_wiki):
        _write_page(tmp_wiki, "concept", "orphan", "Orphan Page")
        # Write an index that doesn't include the page
        _write_index(tmp_wiki, [("concept", "different-page", "Different Page")])
        issues = wiki_lint.cmd_lint()
        assert any("not in index" in i["message"] for i in issues)

    def test_stale_index_entry(self, tmp_wiki):
        # Must have at least one real page so lint doesn't short-circuit
        _write_page(tmp_wiki, "concept", "real-page", "Real Page")
        _write_index(tmp_wiki, [("concept", "real-page", "Real Page"),
                                 ("concept", "ghost", "Ghost Page")])
        issues = wiki_lint.cmd_lint()
        assert any("Stale entry" in i["message"] for i in issues)

    def test_contested_page(self, tmp_wiki):
        _write_page(tmp_wiki, "concept", "contested", "Contested Topic",
                     contested=True,
                     contradictions=["other-page"])
        _write_index(tmp_wiki, [("concept", "contested", "Contested Topic")])
        issues = wiki_lint.cmd_lint()
        assert any("Contested page" in i["message"] for i in issues)

    def test_low_confidence(self, tmp_wiki):
        _write_page(tmp_wiki, "concept", "uncertain", "Uncertain Topic",
                     confidence="low")
        _write_index(tmp_wiki, [("concept", "uncertain", "Uncertain Topic")])
        issues = wiki_lint.cmd_lint()
        assert any("Low confidence" in i["message"] for i in issues)

    def test_broken_wikilink(self, tmp_wiki):
        _write_page(tmp_wiki, "concept", "links-to-missing", "Missing Link",
                     wikilinks=["nonexistent-page"])
        _write_index(tmp_wiki, [("concept", "links-to-missing", "Missing Link")])
        issues = wiki_lint.cmd_lint()
        assert any("Broken [[wikilink]]" in i["message"] for i in issues)

    def test_large_page(self, tmp_wiki):
        _write_page(tmp_wiki, "concept", "large", "Large Page",
                     line_count=250, body="Big page")
        _write_index(tmp_wiki, [("concept", "large", "Large Page")])
        issues = wiki_lint.cmd_lint()
        assert any("Large page" in i["message"] for i in issues)

    def test_fix_regenerates_index(self, tmp_wiki):
        _write_page(tmp_wiki, "concept", "my-page", "My Page")
        # Start with empty index
        _write_index(tmp_wiki, [])
        issues = wiki_lint.cmd_lint(fix=True)
        # After fix, the index should contain the page
        index_path = tmp_wiki / "index.md"
        assert index_path.exists()
        content = index_path.read_text()
        assert "My Page" in content
        assert "concepts/my-page.md" in content

    def test_orphan_page(self, tmp_wiki):
        _write_page(tmp_wiki, "concept", "orphan", "Orphan Page")
        _write_index(tmp_wiki, [("concept", "orphan", "Orphan Page")])
        issues = wiki_lint.cmd_lint()
        orphan_issues = [i for i in issues if "Orphan" in i["message"]]
        assert len(orphan_issues) >= 1


# ── Wiki Sync ───────────────────────────────────────────────────────────────


class TestWikiSync:
    @mock.patch("hpm.wiki.sync.db_module.get_connection")
    @mock.patch("hpm.wiki.sync.db_module.query_recent")
    def test_no_recent_memories(self, mock_query_recent, mock_get_conn, tmp_wiki):
        mock_get_conn.return_value = mock.MagicMock()
        mock_query_recent.return_value = []
        result = wiki_sync.cmd_sync(hours=24)
        assert "No recent memories" in result

    @mock.patch("hpm.wiki.sync.db_module.get_connection")
    @mock.patch("hpm.wiki.sync.db_module.query_recent")
    @mock.patch("hpm.wiki.sync.llm.complete")
    def test_dry_run(self, mock_llm, mock_query_recent, mock_get_conn, tmp_wiki):
        mock_get_conn.return_value = mock.MagicMock()
        mock_query_recent.return_value = [
            {"id": "1", "content": "We use Paddle for payments", "tags": ["project:jarvis", "topic:payments"], "timestamp": "2026-06-24T10:00:00Z"},
            {"id": "2", "content": "Paddle API rate limits", "tags": ["project:jarvis", "topic:payments"], "timestamp": "2026-06-24T11:00:00Z"},
        ]
        mock_llm.return_value = json.dumps([
            {"topic": "Paddle payment processing", "entry_indices": [0, 1]}
        ])

        with mock.patch("hpm.wiki.sync.db_module.init_db"):
            result = wiki_sync.cmd_sync(hours=24, dry_run=True)
            assert "dry-run" in result.lower() or "create" in result

    @mock.patch("hpm.wiki.sync.db_module.get_connection")
    @mock.patch("hpm.wiki.sync.db_module.query_recent")
    @mock.patch("hpm.wiki.sync.llm.complete")
    def test_llm_not_configured(self, mock_llm, mock_query_recent, mock_get_conn, tmp_wiki):
        mock_get_conn.return_value = mock.MagicMock()
        mock_query_recent.return_value = [
            {"id": "1", "content": "Test memory", "tags": [], "timestamp": "2026-06-24T10:00:00Z"}
        ]
        mock_llm.side_effect = ValueError("No API key configured")
        with mock.patch("hpm.wiki.sync.db_module.init_db"):
            result = wiki_sync.cmd_sync(hours=24)
            assert "API key" in result or "setup" in result


# ── Contradiction Awareness (answer.py) ─────────────────────────────────────


class TestContradictionAwareness:
    def test_check_no_wiki(self):
        with mock.patch("hpm.config.WIKI_DIR", Path("/nonexistent/wiki")):
            from hpm.answer import _check_wiki_contradictions
            result = _check_wiki_contradictions("test query")
            assert result == ""

    def test_check_no_contested_pages(self, tmp_wiki):
        _write_page(tmp_wiki, "concept", "normal", "Normal Page",
                     contested=False)
        with mock.patch("hpm.config.WIKI_DIR", tmp_wiki):
            from hpm.answer import _check_wiki_contradictions
            result = _check_wiki_contradictions("normal")
            assert result == ""

    def test_check_contested_match(self, tmp_wiki):
        _write_page(tmp_wiki, "concept", "payment", "Payment Decision",
                     contested=True, contradictions=["old-payment"])
        with mock.patch("hpm.config.WIKI_DIR", tmp_wiki):
            from hpm.answer import _check_wiki_contradictions
            result = _check_wiki_contradictions("payment")
            assert "contested" in result
            assert "Payment Decision" in result
            assert "old-payment" in result

    def test_check_no_match_for_different_query(self, tmp_wiki):
        _write_page(tmp_wiki, "concept", "payment", "Payment Decision",
                     contested=True)
        with mock.patch("hpm.config.WIKI_DIR", tmp_wiki):
            from hpm.answer import _check_wiki_contradictions
            result = _check_wiki_contradictions("gardening")
            assert result == ""
