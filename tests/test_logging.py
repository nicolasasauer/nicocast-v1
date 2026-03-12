"""Tests for NicoCast persistent logging functionality."""

import logging
import logging.handlers
import os
import re
import tempfile

import pytest

from nicocast.config import Config, DEFAULTS
from nicocast.main import _setup_logging


def _reset_root_logger() -> None:
    """Remove all handlers from the root logger between tests."""
    root = logging.getLogger()
    for handler in root.handlers[:]:
        handler.close()
        root.removeHandler(handler)


class TestLoggingConfig:
    """Verify that the new logging config keys are present in defaults."""

    def setup_method(self):
        self.cfg = Config(path="/tmp/nonexistent_logging_test.conf")

    def test_log_file_default_is_set(self):
        assert self.cfg.get("general", "log_file") != ""
        assert "nicocast" in self.cfg.get("general", "log_file")

    def test_log_max_bytes_default_is_positive(self):
        assert self.cfg.getint("general", "log_max_bytes") > 0

    def test_log_backup_count_default_is_positive(self):
        assert self.cfg.getint("general", "log_backup_count") > 0

    def test_log_config_keys_in_defaults(self):
        general = DEFAULTS["general"]
        assert "log_file" in general
        assert "log_max_bytes" in general
        assert "log_backup_count" in general
        assert "operation_mode" in general


class TestSetupLogging:
    """Verify that _setup_logging creates the expected handlers."""

    def teardown_method(self):
        _reset_root_logger()

    def test_console_handler_always_present(self):
        _reset_root_logger()
        _setup_logging("INFO", log_file="")
        root = logging.getLogger()
        stream_handlers = [
            h for h in root.handlers
            if isinstance(h, logging.StreamHandler)
            and not isinstance(h, logging.handlers.RotatingFileHandler)
        ]
        assert len(stream_handlers) >= 1

    def test_file_handler_created_when_log_file_configured(self, tmp_path):
        _reset_root_logger()
        log_path = str(tmp_path / "test.log")
        _setup_logging("INFO", log_file=log_path, max_bytes=1024, backup_count=2)
        root = logging.getLogger()
        file_handlers = [
            h for h in root.handlers
            if isinstance(h, logging.handlers.RotatingFileHandler)
        ]
        assert len(file_handlers) == 1
        assert file_handlers[0].baseFilename == log_path

    def test_no_file_handler_when_log_file_empty(self):
        _reset_root_logger()
        _setup_logging("INFO", log_file="")
        root = logging.getLogger()
        file_handlers = [
            h for h in root.handlers
            if isinstance(h, logging.handlers.RotatingFileHandler)
        ]
        assert len(file_handlers) == 0

    def test_log_file_directory_is_created(self, tmp_path):
        _reset_root_logger()
        nested_dir = tmp_path / "a" / "b"
        log_path = str(nested_dir / "nicocast.log")
        _setup_logging("INFO", log_file=log_path)
        assert nested_dir.exists()

    def test_messages_written_to_log_file(self, tmp_path):
        _reset_root_logger()
        log_path = str(tmp_path / "nicocast.log")
        _setup_logging("DEBUG", log_file=log_path)

        test_logger = logging.getLogger("nicocast.test_persistent")
        test_logger.info("persistent-log-test-marker")

        # Flush and close file handlers so content is flushed to disk
        root = logging.getLogger()
        for h in root.handlers:
            h.flush()

        with open(log_path, "r", encoding="utf-8") as fh:
            content = fh.read()

        assert "persistent-log-test-marker" in content

    def test_log_level_respected(self, tmp_path):
        _reset_root_logger()
        log_path = str(tmp_path / "nicocast.log")
        _setup_logging("WARNING", log_file=log_path)

        test_logger = logging.getLogger("nicocast.level_test")
        test_logger.debug("should-not-appear")
        test_logger.warning("should-appear")

        root = logging.getLogger()
        for h in root.handlers:
            h.flush()

        with open(log_path, "r", encoding="utf-8") as fh:
            content = fh.read()

        assert "should-not-appear" not in content
        assert "should-appear" in content

    def test_rotating_file_handler_max_bytes(self, tmp_path):
        _reset_root_logger()
        log_path = str(tmp_path / "rotate.log")
        _setup_logging("DEBUG", log_file=log_path, max_bytes=200, backup_count=3)

        root = logging.getLogger()
        file_handlers = [
            h for h in root.handlers
            if isinstance(h, logging.handlers.RotatingFileHandler)
        ]
        assert file_handlers[0].maxBytes == 200
        assert file_handlers[0].backupCount == 3

    def test_invalid_log_file_path_does_not_raise(self):
        """A completely invalid path should not crash the application."""
        _reset_root_logger()
        # /proc/nonexistent/deeply/nested is not writable
        _setup_logging("INFO", log_file="/proc/nonexistent/deeply/nested/nicocast.log")
        # If we reach here, the exception was handled gracefully
        assert True

    def test_log_format_includes_timestamp_and_level(self, tmp_path):
        _reset_root_logger()
        log_path = str(tmp_path / "nicocast.log")
        _setup_logging("INFO", log_file=log_path)

        test_logger = logging.getLogger("nicocast.format_test")
        test_logger.info("format-check")

        root = logging.getLogger()
        for h in root.handlers:
            h.flush()

        with open(log_path, "r", encoding="utf-8") as fh:
            content = fh.read()

        assert re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", content)
        assert "INFO" in content
        assert "nicocast.format_test" in content
