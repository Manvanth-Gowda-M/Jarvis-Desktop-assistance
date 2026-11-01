"""Tests for the web research tool (mocked — no live network in CI)."""

import pytest
from unittest.mock import patch, MagicMock

from assistant.tools import research_web


class TestResearchWebDDGSLibrary:
    """Tests exercising the primary duckduckgo_search.DDGS path."""

    @patch("assistant.tools.DDGS")
    def test_returns_formatted_results(self, mock_ddgs_cls):
        mock_ddgs = MagicMock()
        mock_ddgs.text.return_value = [
            {
                "title": "Python Language",
                "href": "https://python.org",
                "body": "Python is a high-level programming language.",
            },
            {
                "title": "Wikipedia: Python",
                "href": "https://en.wikipedia.org/wiki/Python",
                "body": "Python is widely used in data science and web development.",
            },
        ]
        mock_ddgs_cls.return_value = mock_ddgs

        result = research_web("Python programming language")

        assert "Python programming language" in result
        assert "python.org" in result
        assert "Wikipedia" in result
        assert "1." in result
        assert "2." in result

    @patch("assistant.tools.DDGS")
    def test_returns_source_urls(self, mock_ddgs_cls):
        mock_ddgs = MagicMock()
        mock_ddgs.text.return_value = [
            {
                "title": "FastAPI",
                "href": "https://fastapi.tiangolo.com",
                "body": "FastAPI is a modern web framework for Python.",
            },
        ]
        mock_ddgs_cls.return_value = mock_ddgs

        result = research_web("FastAPI framework")
        assert "https://fastapi.tiangolo.com" in result
        assert "Source:" in result

    @patch("assistant.tools.DDGS")
    def test_empty_query_rejected(self, mock_ddgs_cls):
        result = research_web("")
        assert "Please provide" in result
        mock_ddgs_cls.assert_not_called()

    @patch("assistant.tools.DDGS")
    def test_whitespace_only_query_rejected(self, mock_ddgs_cls):
        result = research_web("   ")
        assert "Please provide" in result

    @patch("assistant.tools.DDGS")
    def test_handles_ddgs_exception_gracefully(self, mock_ddgs_cls):
        """If DDGS raises, research_web falls through to fallback or error message."""
        mock_ddgs_cls.side_effect = Exception("DDGS network error")

        # Should not raise — should return a result or fallback
        result = research_web("test query")
        assert isinstance(result, str)
        assert len(result) > 0

    @patch("assistant.tools.DDGS")
    def test_handles_empty_ddgs_results(self, mock_ddgs_cls):
        mock_ddgs = MagicMock()
        mock_ddgs.text.return_value = []
        mock_ddgs_cls.return_value = mock_ddgs

        result = research_web("very obscure topic xyzzy123")
        assert isinstance(result, str)


class TestResearchWebFallback:
    """Tests for the HTML/API fallback paths."""

    @patch("assistant.tools.requests.get")
    @patch("assistant.tools.requests.post")
    @patch("assistant.tools.DDGS", side_effect=ImportError("no module"))
    def test_fallback_to_ddg_api(self, mock_ddgs, mock_post, mock_get):
        """When DDGS library fails, falls through to HTTP scraping."""
        mock_post.return_value = MagicMock(status_code=403, text="Blocked")

        mock_api_resp = MagicMock()
        mock_api_resp.status_code = 200
        mock_api_resp.json.return_value = {
            "AbstractText": "Python is a programming language.",
            "AbstractURL": "https://en.wikipedia.org/wiki/Python",
            "Heading": "Python (programming language)",
        }
        mock_get.return_value = mock_api_resp

        result = research_web("Python")
        assert "Python" in result

    @patch("assistant.tools.requests.get", side_effect=Exception("Network down"))
    @patch("assistant.tools.requests.post", side_effect=Exception("Network down"))
    @patch("assistant.tools.DDGS", side_effect=ImportError("no module"))
    def test_total_network_failure(self, mock_ddgs, mock_post, mock_get):
        """All methods fail — should return an error string, not crash."""
        result = research_web("anything")
        assert isinstance(result, str)
        assert "unavailable" in result.lower() or "no direct search" in result.lower()

