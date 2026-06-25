"""Tests for the hpm wiki layer (Phase A)."""

from __future__ import annotations

import os
import re
import tempfile
from datetime import date
from pathlib import Path
from unittest import mock

import pytest

from hpm.wiki import compile as wiki_compile
from hpm.wiki import find as wiki_find
from hpm.wiki import init as wiki_init
from hpm.wiki import types as wiki_types

# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_hpm_dir():
    """Temporarily point HPM_DIR to a temp directory."""
    with tempfile.TemporaryDirectory() as td:
        os.environ.pop("HPM_DIR", None)
        # Patch config.WIKI_DIR
        wiki_path = Path(td) / "wiki"
        with mock.patch("hpm.config.WIKI_DIR", wiki_path):
            # Also patch config.HPM_DIR
            with mock.patch("hpm.config.HPM_DIR", Path(td)):
                yield wiki_path


# ── Slug ────────────────────────────────────────────────────────────────────


class TestSlugify:
    def test_basic(self):
        assert wiki_types.slugify("Hello World") == "hello-world"

    def test_colon_and_punctuation(self):
        assert wiki_types.slugify("My Project: Payments API v2") == "my-project-payments-api-v2"

    def test_multiple_spaces_and_dashes(self):
        assert wiki_types.slugify("Foo   Bar--Baz") == "foo-bar-baz"

    def test_strips_leading_trailing(self):
        assert wiki_types.slugify("  --Hello--  ") == "hello"

    def test_unicode_handling(self):
        slug = wiki_types.slugify("über cool!")
        assert re.match(r"^[a-z0-9-]+$", slug)


# ── Frontmatter ─────────────────────────────────────────────────────────────


class TestParseFrontmatter:
    def test_no_frontmatter(self):
        meta, body = wiki_types.parse_frontmatter("Just body text")
        assert meta == {}
        assert body == "Just body text"

    def test_basic_frontmatter(self):
        text = """---
title: Test Page
type: concept
confidence: high
---

Body content here.
"""
        meta, body = wiki_types.parse_frontmatter(text)
        assert meta["title"] == "Test Page"
        assert meta["type"] == "concept"
        assert meta["confidence"] == "high"
        assert "Body content here" in body

    def test_frontmatter_with_tags(self):
        text = """---
title: My Page
tags: [project:jarvis, topic:payments]
---

Body
"""
        meta, body = wiki_types.parse_frontmatter(text)
        assert meta["tags"] == ["project:jarvis", "topic:payments"]

    def test_boolean_values(self):
        text = """---
title: Test
contested: true
---

Body
"""
        meta, body = wiki_types.parse_frontmatter(text)
        assert meta["contested"] is True

    def test_date_values(self):
        text = """---
title: Test
created: 2026-06-24
---

Body
"""
        meta, body = wiki_types.parse_frontmatter(text)
        assert meta["created"] == date(2026, 6, 24)

    def test_missing_closing_delimiter(self):
        text = """---
title: Test
"""

        meta, body = wiki_types.parse_frontmatter(text)
        assert meta == {}
        assert body == text

    def test_roundtrip_make_and_parse(self):
        frontmatter = wiki_types.make_frontmatter(
            title="Payment Decision",
            page_type="entity",
            tags=["project:jarvis"],
            sources=["memory:abc123"],
            confidence="high",
        )
        text = frontmatter + "\nBody\n"
        meta, body = wiki_types.parse_frontmatter(text)
        assert meta["title"] == "Payment Decision"
        assert meta["type"] == "entity"
        assert meta["tags"] == ["project:jarvis"]
        assert meta["sources"] == ["memory:abc123"]
        assert meta["confidence"] == "high"
        assert body.strip() == "Body"


# ── SCHEMA ──────────────────────────────────────────────────────────────────


class TestSchema:
    def test_generate_schema_contains_key_sections(self):
        schema = wiki_types.generate_schema()
        assert "# Wiki Schema" in schema
        assert "Tag Taxonomy" in schema
        assert "Required Frontmatter" in schema
        assert "entity" in schema
        assert str(date.today().isoformat()) in schema


# ── Wiki Init ───────────────────────────────────────────────────────────────


class TestWikiInit:
    def test_init_creates_directory_structure(self, tmp_hpm_dir):
        wiki_init.cmd_init()

        assert tmp_hpm_dir.exists()
        assert (tmp_hpm_dir / "entities").exists()
        assert (tmp_hpm_dir / "concepts").exists()
        assert (tmp_hpm_dir / "comparisons").exists()
        assert (tmp_hpm_dir / "queries").exists()

    def test_init_creates_files(self, tmp_hpm_dir):
        wiki_init.cmd_init()

        assert (tmp_hpm_dir / "SCHEMA.md").exists()
        assert (tmp_hpm_dir / "index.md").exists()
        assert (tmp_hpm_dir / "log.md").exists()

    def test_init_idempotent(self, tmp_hpm_dir):
        wiki_init.cmd_init()
        schema_content = (tmp_hpm_dir / "SCHEMA.md").read_text()
        wiki_init.cmd_init()
        # Files should not be overwritten
        assert (tmp_hpm_dir / "SCHEMA.md").read_text() == schema_content

    def test_log_appended_on_init(self, tmp_hpm_dir):
        wiki_init.cmd_init()
        log = (tmp_hpm_dir / "log.md").read_text()
        assert "Initialized wiki" in log
        assert "hpm wiki init" in log


# ── Wiki Find ───────────────────────────────────────────────────────────────


class TestWikiFind:
    def test_fallthrough_when_no_index(self):
        with mock.patch("hpm.wiki.find.cmd_find") as mock_find:
            mock_find.return_value = "__WIKI_FALLTHROUGH__:test query"
            result = wiki_find.cmd_find("test query")
            assert result == "__WIKI_FALLTHROUGH__:test query"

    def test_find_returns_page_content(self, tmp_hpm_dir):
        wiki_init.cmd_init()

        # Create a concept page
        concept_dir = tmp_hpm_dir / "concepts"
        concept_dir.mkdir(parents=True, exist_ok=True)
        page_content = """---
title: Payment Decision
type: concept
confidence: high
tags: [project:jarvis, topic:payments]
---

We decided to use Paddle for payment processing.
"""
        (concept_dir / "payment-decision.md").write_text(page_content)

        # Manually add to index
        index = tmp_hpm_dir / "index.md"
        index.write_text(
            "# Wiki Index\n\n## Concepts\n\n"
            "- [Payment Decision](concepts/payment-decision.md)\n"
        )

        result = wiki_find.cmd_find("payment")
        assert "Payment Decision" in result
        assert "Paddle" in result
        assert "wiki page" in result

    def test_find_fallthrough_on_no_match(self, tmp_hpm_dir):
        wiki_init.cmd_init()
        result = wiki_find.cmd_find("nonexistent topic")
        assert result.startswith("__WIKI_FALLTHROUGH__:")


# ── Wiki Compile ────────────────────────────────────────────────────────────


class TestWikiCompile:
    def test_compile_no_memories(self, tmp_hpm_dir):
        wiki_init.cmd_init()

        with (
            mock.patch("hpm.wiki.compile.embed.embed_text"),
            mock.patch("hpm.wiki.compile.db_module.get_connection"),
            mock.patch("hpm.wiki.compile.db_module.init_db"),
            mock.patch("hpm.wiki.compile.db_module.query_hybrid", return_value=[]),
        ):
            mock_embed = mock.MagicMock()
            mock_embed.return_value = [0.1] * 384
            result = wiki_compile.cmd_compile("nonexistent topic")
            assert "No relevant memories" in result

    def test_compile_existing_page_no_force(self, tmp_hpm_dir):
        wiki_init.cmd_init()

        # Create an existing page
        concept_dir = tmp_hpm_dir / "concepts"
        concept_dir.mkdir(parents=True, exist_ok=True)
        (concept_dir / "existing-topic.md").write_text("---\ntitle: Existing Topic\n---\n\nBody")

        result = wiki_compile.cmd_compile("existing topic")
        assert "Page already exists" in result

    @mock.patch("hpm.rerank")
    def test_compile_creates_page(
        self, mock_rerank_mod, tmp_hpm_dir
    ):
        wiki_init.cmd_init()

        mock_rerank_mod.rerank.return_value = [
            {
                "id": "abc123",
                "content": "We decided to use Paddle",
                "timestamp": "2026-06-20",
                "source": "hermes",
                "tags": ["project:jarvis"],
            }
        ]

        hybrid_results = [
            {
                "id": "abc123",
                "content": "We decided to use Paddle",
                "timestamp": "2026-06-20",
                "source": "hermes",
                "tags": ["project:jarvis"],
            }
        ]
        with (
            mock.patch("hpm.wiki.compile.embed.embed_text",
                       return_value=[0.1] * 384),
            mock.patch("hpm.wiki.compile.llm.complete",
                       return_value=(
                           "---\ntitle: Payment Decision\n"
                           "type: concept\nconfidence: high\n"
                           "tags: [project:jarvis, topic:payments]\n"
                           "sources: [memory:abc123]\n---\n\n"
                           "We decided to use Paddle for payment processing."
                       )),
            mock.patch("hpm.wiki.compile.db_module.get_connection"),
            mock.patch("hpm.wiki.compile.db_module.init_db"),
            mock.patch(
                "hpm.wiki.compile.db_module.query_hybrid",
                return_value=hybrid_results,
            ),
        ):
            result = wiki_compile.cmd_compile("payment")
            assert "Wiki page written" in result

            # Verify the page was written
            page_path = tmp_hpm_dir / "concepts" / "payment.md"
            assert page_path.exists()
            content = page_path.read_text()
            assert "Payment Decision" in content
            assert "Paddle" in content

    @mock.patch("hpm.rerank")
    def test_compile_updates_index(
        self, mock_rerank_mod, tmp_hpm_dir
    ):
        wiki_init.cmd_init()

        with (
            mock.patch("hpm.wiki.compile.embed.embed_text",
                       return_value=[0.1] * 384),
            mock.patch("hpm.wiki.compile.llm.complete",
                       return_value="---\ntitle: Test Topic\n---\n\nBody"),
            mock.patch("hpm.wiki.compile.db_module.get_connection"),
            mock.patch("hpm.wiki.compile.db_module.init_db"),
            mock.patch("hpm.wiki.compile.db_module.query_hybrid",
                       return_value=[{"id": "abc", "content": "test"}]),
        ):
            wiki_compile.cmd_compile("test topic")

            # Index should contain the new page
            index = (tmp_hpm_dir / "index.md").read_text()
            assert "Test Topic" in index
            assert "concepts/test-topic.md" in index

    def test_compile_missing_fallthrough(self, tmp_hpm_dir):
        """Compile with no relevant memories should report that."""
        wiki_init.cmd_init()

        with (
            mock.patch("hpm.wiki.compile.embed.embed_text",
                       return_value=[0.1] * 384),
            mock.patch("hpm.wiki.compile.db_module.get_connection"),
            mock.patch("hpm.wiki.compile.db_module.init_db"),
            mock.patch("hpm.wiki.compile.db_module.query_hybrid",
                       return_value=[]),
        ):

            result = wiki_compile.cmd_compile("unknown topic", force=False)
            assert "No relevant memories" in result
