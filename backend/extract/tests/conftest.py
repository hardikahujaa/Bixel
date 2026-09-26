"""Pytest configuration for the extraction tests.

Registers the ``live`` marker here rather than in a shared root config, so nothing outside
this module needs changing. Live tests call real Gemini and are skipped unless
``BIXEL_LIVE_LLM=1`` -- the suite a judge runs must not need an API key.
"""
import os

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "live: hits the real Gemini API; runs only when BIXEL_LIVE_LLM=1"
    )


def pytest_collection_modifyitems(config, items):
    if os.environ.get("BIXEL_LIVE_LLM") == "1":
        return
    skip = pytest.mark.skip(reason="needs BIXEL_LIVE_LLM=1 (calls real Gemini)")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)
