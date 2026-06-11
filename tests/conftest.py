"""Shared fixtures for FireCloud tests."""

import os

import pytest


@pytest.fixture
def tmp_dir(tmp_path):
    return tmp_path


@pytest.fixture
def storage_dir(tmp_path):
    d = tmp_path / "storage"
    d.mkdir()
    return d


@pytest.fixture
def test_passphrase():
    return "test-passphrase-do-not-use-in-production"


@pytest.fixture
def network_key():
    """Deterministic 256-bit test key — NOT for production."""
    return b"\x01" * 32


@pytest.fixture
def sample_file(tmp_path):
    f = tmp_path / "sample.txt"
    content = b"Hello, FireCloud! " * 1000  # ~18KB, enough for multiple chunks
    f.write_bytes(content)
    return f


@pytest.fixture
def large_sample_file(tmp_path):
    f = tmp_path / "large_sample.bin"
    content = os.urandom(256 * 1024)
    f.write_bytes(content)
    return f
