"""
Flask-based settings web UI for NicoCast.

Accessible at http://<pi-ip>:8080/ after the service starts.
Provides a simple interface to:
  - View connection status
  - Change device name
  - Change WPS PIN
  - Adjust video resolution preference
  - Restart the service
"""

import threading
import logging
import os

logger = logging.getLogger(__name__)

try:
    from flask import Flask, render_template, request, redirect, url_for, jsonify
    _FLASK_AVAILABLE = True
except ImportError:
    _FLASK_AVAILABLE = False
    logger.warning("Flask not installed – web UI disabled. Run: pip install flask")


class WebUI:
    """Runs the Flask web UI in a background daemon thread."""

    def __init__(self, config, status_provider=None, restart_callback=None):
        """
        Args:
            config:           :class:`~nicocast.config.Config` instance.
            status_provider:  Callable that returns a status dict (optional).
            restart_callback: Callable invoked when the user clicks "Restart".
        """
        self.config = config
        self.status_provider = status_provider or (lambda: {})
        self.restart_callback = restart_callback
        self._thread: threading.Thread | None = None
        self._app: "Flask | None" = None

    def start(self) -> None:
        if not self.config.getbool("webui", "enabled"):
            logger.info("Web UI disabled in config")
            return
        if not _FLASK_AVAILABLE:
            logger.warning("Flask not available – web UI not started")
            return

        self._app = self._create_app()
        port = self.config.getint("webui", "port")
        bind = self.config.get("webui", "bind")
        self._thread = threading.Thread(
            target=self._run, args=(bind, port), daemon=True, name="webui"
        )
        self._thread.start()
        logger.info("Web UI available at http://%s:%d/", bind, port)

    def stop(self) -> None:
        # Flask dev server cannot be stopped cleanly from another thread;
        # the daemon thread will die when the process exits.
        pass

    # ─── Flask app factory ────────────────────────────────────────────────────

    def _create_app(self) -> "Flask":
        templates_dir = os.path.join(os.path.dirname(__file__), "templates")
        app = Flask(__name__, template_folder=templates_dir)
        app.secret_key = os.urandom(24)

        config = self.config
        status_provider = self.status_provider
        restart_cb = self.restart_callback

        @app.route("/", methods=["GET"])
        def index():
            status = status_provider()
            cfg = config.as_dict()
            return render_template("index.html", status=status, cfg=cfg)

        @app.route("/settings", methods=["POST"])
        def save_settings():
            form = request.form

            # Sanitise and apply each field
            device_name = form.get("device_name", "").strip()
            if device_name:
                config.set("general", "device_name", device_name)

            pin = form.get("pin", "").strip()
            if pin and pin.isdigit() and len(pin) in (4, 8):
                config.set("general", "pin", pin)

            conn_method = form.get("connection_method", "").strip()
            if conn_method in ("pbc", "pin"):
                config.set("general", "connection_method", conn_method)

            video_sink = form.get("video_sink", "").strip()
            if video_sink:
                config.set("display", "video_sink", video_sink)

            audio_output = form.get("audio_output", "").strip()
            if audio_output:
                config.set("display", "audio_output", audio_output)

            log_level = form.get("log_level", "").strip()
            if log_level in ("DEBUG", "INFO", "WARNING", "ERROR"):
                config.set("general", "log_level", log_level)

            try:
                config.save()
            except Exception as exc:
                logger.error("Failed to save config: %s", exc)

            return redirect(url_for("index"))

        @app.route("/restart", methods=["POST"])
        def restart():
            if restart_cb:
                threading.Thread(
                    target=restart_cb, daemon=True
                ).start()
            return redirect(url_for("index"))

        @app.route("/api/status")
        def api_status():
            return jsonify(status_provider())

        return app

    def _run(self, host: str, port: int) -> None:
        """Run the Flask development server (blocking)."""
        self._app.run(host=host, port=port, debug=False, use_reloader=False)
