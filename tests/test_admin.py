"""Tests for the /admin dashboard (palier 1) and source management (palier 2).

These avoid the network, the Anthropic SDK and xelatex. They exercise the
read-only dashboard plumbing and (palier 2) the sources config module.

    python -m unittest discover -s tests
"""
from __future__ import annotations

import os
import tempfile
import unittest
from importlib import reload
from pathlib import Path


os.environ.setdefault("PAPERNEWS_NO_SCHED", "1")


def _reload_web(state: Path, config: Path, cache: Path):
    os.environ["PAPERNEWS_STATE"] = str(state)
    os.environ["PAPERNEWS_CONFIG"] = str(config)
    os.environ["PAPERNEWS_CACHE"] = str(cache)
    import papernews.web as web
    return reload(web)


_SAMPLE_TOML = """
[[source]]
name = "Quanta Magazine"
kind = "rss"
url = "https://www.quantamagazine.org/feed/"
limit = 8
"""


# --- _article_status -------------------------------------------------------

class ArticleStatusTests(unittest.TestCase):
    def test_all_five_stages(self):
        import papernews.web as web
        base = {"text": "t", "summary": "s", "body": "b", "rendered_at": "2026-06-18"}
        self.assertEqual(web._article_status({**base, "text": None}), "unreadable")
        self.assertEqual(web._article_status({**base, "summary": None}), "pending summary")
        self.assertEqual(web._article_status({**base, "body": None}), "pending rewrite")
        self.assertEqual(web._article_status({**base, "rendered_at": None}), "ready")
        self.assertEqual(web._article_status(base), "rendered")


# --- Store.per_source_counts / recent --------------------------------------

class StoreDashboardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        from papernews.store import Store
        self.store = Store(Path(self.tmp.name) / "state.db")
        # One readable + summarized + rendered, one unreadable.
        self.store.insert_raw("Quanta Magazine", "https://x/1", "A", text="hello")
        self.store.set_summary(self.store_hash("https://x/1"), "sum")
        self.store.set_body(self.store_hash("https://x/1"), "body")
        self.store.mark_rendered([self.store_hash("https://x/1")], "2026-06-18")
        self.store.insert_raw("Hacker News", "https://x/2", "B", text=None)

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def store_hash(url):
        from papernews.store import _url_hash
        return _url_hash(url)

    def test_per_source_counts(self):
        pc = self.store.per_source_counts()
        self.assertEqual(pc["Quanta Magazine"]["total"], 1)
        self.assertEqual(pc["Quanta Magazine"]["rendered"], 1)
        self.assertEqual(pc["Hacker News"]["unreadable"], 1)

    def test_recent_newest_first_and_columns(self):
        rows = self.store.recent(10)
        self.assertEqual(len(rows), 2)
        # Each row exposes the columns _article_status needs.
        for col in ("text", "summary", "body", "rendered_at", "source", "title"):
            self.assertIn(col, rows[0].keys())


# --- GET /admin ------------------------------------------------------------

class AdminPageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.config = d / "sources.toml"
        self.config.write_text(_SAMPLE_TOML)
        self.cache = d / "cache"
        # Seed the store at the path the app will read.
        from papernews.store import Store
        st = Store(d / "state.db")
        st.insert_raw("Quanta Magazine", "https://x/1", "Hello world", text="body")
        self.web = _reload_web(d / "state.db", self.config, self.cache)

    def tearDown(self):
        self.tmp.cleanup()

    def test_admin_page_renders(self):
        client = self.web.app.test_client()
        r = client.get("/admin")
        self.assertEqual(r.status_code, 200)
        html = r.get_data(as_text=True)
        self.assertIn("Pipeline", html)
        self.assertIn("Quanta Magazine", html)
        self.assertIn("Hello world", html)


# --- config.py (palier 2) --------------------------------------------------

class SourcesConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "sources.toml"
        self.path.write_text(_SAMPLE_TOML)

    def tearDown(self):
        self.tmp.cleanup()

    def test_load_sources_defaults_enabled(self):
        from papernews import config
        srcs = config.load_sources(self.path)
        self.assertEqual(len(srcs), 1)
        self.assertTrue(srcs[0]["enabled"])

    def test_add_rss_then_roundtrip_via_tomllib(self):
        import tomllib
        from papernews import config
        config.add_source(self.path, {
            "name": "Lobsters", "kind": "rss",
            "url": "https://lobste.rs/rss", "limit": 12,
        })
        with open(self.path, "rb") as f:
            data = tomllib.load(f)
        names = [s["name"] for s in data["source"]]
        self.assertIn("Lobsters", names)
        self.assertIn("Quanta Magazine", names)

    def test_add_duplicate_name_rejected(self):
        from papernews import config
        with self.assertRaises(config.SourceError):
            config.add_source(self.path, {
                "name": "Quanta Magazine", "kind": "rss",
                "url": "https://example.com/feed",
            })

    def test_add_rss_without_url_rejected(self):
        from papernews import config
        with self.assertRaises(config.SourceError):
            config.add_source(self.path, {"name": "NoURL", "kind": "rss"})

    def test_toggle_and_active_sources(self):
        from papernews import config
        self.assertTrue(config.set_enabled(self.path, "Quanta Magazine", False))
        self.assertEqual(config.active_sources(self.path), [])
        config.set_enabled(self.path, "Quanta Magazine", True)
        self.assertEqual(len(config.active_sources(self.path)), 1)

    def test_remove_source(self):
        from papernews import config
        self.assertTrue(config.remove_source(self.path, "Quanta Magazine"))
        self.assertEqual(config.load_sources(self.path), [])
        self.assertFalse(config.remove_source(self.path, "Quanta Magazine"))

    def test_validate_feed_uses_feedparser(self):
        from unittest import mock
        from papernews import config

        class _Feed:
            entries = [mock.Mock(title="Story one"), mock.Mock(title="Story two")]
            feed = mock.Mock(title="Test Feed")
            bozo = 0

        with mock.patch("feedparser.parse", return_value=_Feed()):
            res = config.validate_feed("https://x/feed")
        self.assertTrue(res["ok"])
        self.assertEqual(res["n_entries"], 2)

        class _Empty:
            entries = []
            bozo = 1
            bozo_exception = "boom"

        with mock.patch("feedparser.parse", return_value=_Empty()):
            res = config.validate_feed("https://x/bad")
        self.assertFalse(res["ok"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
