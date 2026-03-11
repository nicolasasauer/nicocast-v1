"""
GStreamer display pipeline for NicoCast.

Builds and launches a GStreamer pipeline that:
  1. Receives an H.264 RTP stream on a UDP port
  2. Decodes it using hardware acceleration when available
     (v4l2h264dec on Raspberry Pi Zero 2W via the Video4Linux2 M2M interface)
  3. Outputs the video to the HDMI display (kmssink / autovideosink)
  4. Optionally receives and plays AAC or LPCM audio

On Raspberry Pi Zero 2W running Raspberry Pi OS the preferred decode element
is v4l2h264dec, but the pipeline falls back gracefully to avdec_h264 (software).

Pipeline (video only):
    udpsrc → rtpjitterbuffer → rtph264depay → h264parse
        → [v4l2h264dec | avdec_h264] → videoconvert → [kmssink | autovideosink]

Pipeline (audio, AAC):
    udpsrc → rtpjitterbuffer → rtpmp4adepay → aacparse → avdec_aac
        → audioconvert → [alsasink | autoaudiosink]
"""

import shlex
import subprocess
import threading
import logging
import shutil
import os

logger = logging.getLogger(__name__)


class DisplayPipeline:
    """Manages a GStreamer subprocess for video/audio playback."""

    def __init__(self, config):
        self.config = config
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._running = False

    # ─── Public API ───────────────────────────────────────────────────────────

    def start(
        self,
        rtp_port: int,
        video_format: str = "",
        audio_codecs: str = "",
    ) -> None:
        """Build and launch the GStreamer pipeline.

        Args:
            rtp_port:     UDP port carrying the RTP/H.264 video stream.
            video_format: WFD video_formats string (used for resolution hints).
            audio_codecs: WFD audio_codecs string (used to select audio decoder).
        """
        with self._lock:
            if self._proc and self._proc.poll() is None:
                logger.info("Pipeline already running – restarting")
                self._stop_proc()

            pipeline = self._build_pipeline(rtp_port, video_format, audio_codecs)
            logger.info("Launching GStreamer pipeline:\n  %s", pipeline)

            gst_bin = shutil.which("gst-launch-1.0")
            if not gst_bin:
                logger.error(
                    "gst-launch-1.0 not found. "
                    "Install it with: sudo apt install gstreamer1.0-tools"
                )
                return

            env = dict(os.environ)
            env.setdefault("GST_DEBUG", "1")

            self._proc = subprocess.Popen(
                [gst_bin] + shlex.split(pipeline),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
            )
            self._running = True
            # Log GStreamer stderr in the background
            threading.Thread(
                target=self._log_output, daemon=True, name="gst-log"
            ).start()
            logger.info("GStreamer pipeline started (PID %d)", self._proc.pid)

    def stop(self) -> None:
        """Stop the GStreamer pipeline."""
        with self._lock:
            self._running = False
            self._stop_proc()

    def is_running(self) -> bool:
        """Return True if the pipeline subprocess is alive."""
        with self._lock:
            return bool(self._proc and self._proc.poll() is None)

    # ─── Pipeline construction ────────────────────────────────────────────────

    def _build_pipeline(
        self, rtp_port: int, video_format: str, audio_codecs: str
    ) -> str:
        jitter_ms = self.config.get("miracast", "jitter_buffer_ms")
        hw_decode = self.config.getbool("display", "hw_decode")
        video_sink = self.config.get("display", "video_sink")
        fullscreen = self.config.getbool("display", "fullscreen")
        extra = self.config.get("display", "extra_pipeline_opts").strip()
        audio_output = self.config.get("display", "audio_output")

        # ── Video source & depay ─────────────────────────────────────────────
        video_caps = (
            "application/x-rtp,"
            "media=video,"
            "clock-rate=90000,"
            "encoding-name=H264,"
            "payload=33"
        )
        video_src = (
            f"udpsrc port={rtp_port} buffer-size=524288 "
            f'caps="{video_caps}"'
        )

        # ── Decoder ──────────────────────────────────────────────────────────
        decoder = self._select_video_decoder(hw_decode)

        # ── Video sink ───────────────────────────────────────────────────────
        vsink = self._select_video_sink(video_sink, fullscreen)

        # ── Build video branch ───────────────────────────────────────────────
        video_branch = (
            f"{video_src} ! "
            f"rtpjitterbuffer latency={jitter_ms} ! "
            f"rtph264depay ! "
            f"h264parse ! "
            f"{decoder} ! "
            f"videoconvert ! "
            f"{vsink} sync=false"
        )

        # ── Audio branch (optional) ───────────────────────────────────────────
        audio_branch = ""
        if audio_output != "disabled" and audio_codecs:
            audio_branch = self._build_audio_branch(audio_codecs, audio_output)

        if audio_branch:
            return f"{video_branch}    {audio_branch}"
        return video_branch

    def _select_video_decoder(self, hw_decode: bool) -> str:
        """Return the best available H.264 GStreamer decoder element."""
        if hw_decode:
            # Prefer V4L2 hardware decoder (Raspberry Pi Zero 2W / Pi 3+)
            if self._gst_element_exists("v4l2h264dec"):
                return "v4l2h264dec"
            # Legacy OpenMAX decoder (older Raspberry Pi OS / Pi 1/2/3)
            if self._gst_element_exists("omxh264dec"):
                return "omxh264dec"
        # Software fallback
        return "avdec_h264"

    @staticmethod
    def _select_video_sink(sink_setting: str, fullscreen: bool) -> str:
        fs_opt = ""
        if fullscreen:
            fs_opt = " fullscreen=true"
        if sink_setting == "auto":
            return f"autovideosink{fs_opt}"
        if sink_setting == "kmssink":
            return f"kmssink{fs_opt}"
        if sink_setting == "fbdevsink":
            return "fbdevsink"
        if sink_setting == "fakesink":
            return "fakesink"
        # ximagesink and others
        return f"{sink_setting}{fs_opt}"

    def _build_audio_branch(
        self, audio_codecs: str, audio_output: str
    ) -> str:
        """Return an audio GStreamer branch string (or empty string)."""
        audio_rtp_port = self.config.getint("miracast", "audio_rtp_port")
        if audio_rtp_port == 0:
            return ""

        # Determine codec from the negotiated audio_codecs string
        codecs_lower = audio_codecs.lower()
        if "aac" in codecs_lower:
            audio_caps = (
                "application/x-rtp,"
                "media=audio,"
                "clock-rate=44100,"
                "encoding-name=MPEG4-GENERIC"
            )
            depay = "rtpmp4adepay ! aacparse ! avdec_aac"
        elif "lpcm" in codecs_lower:
            audio_caps = (
                "application/x-rtp,"
                "media=audio,"
                "clock-rate=48000,"
                "encoding-name=L16"
            )
            depay = "rtpL16depay"
        elif "ac3" in codecs_lower:
            audio_caps = (
                "application/x-rtp,"
                "media=audio,"
                "clock-rate=48000,"
                "encoding-name=AC3"
            )
            depay = "rtpac3depay ! avdec_ac3"
        else:
            return ""  # Unknown codec

        asink = self._select_audio_sink(audio_output)
        return (
            f"    udpsrc port={audio_rtp_port} "
            f'caps="{audio_caps}" ! '
            f"rtpjitterbuffer ! "
            f"{depay} ! "
            f"audioconvert ! "
            f"audioresample ! "
            f"{asink}"
        )

    @staticmethod
    def _select_audio_sink(audio_output: str) -> str:
        if audio_output in ("hdmi", "auto"):
            return "autoaudiosink"
        if audio_output == "headphone":
            return "alsasink device=hw:0"
        return "autoaudiosink"

    # ─── Subprocess helpers ───────────────────────────────────────────────────

    def _stop_proc(self) -> None:
        if self._proc:
            if self._proc.poll() is None:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
            self._proc = None

    def _log_output(self) -> None:
        """Stream GStreamer stderr to Python logger."""
        if not self._proc or not self._proc.stderr:
            return
        for line in self._proc.stderr:
            line = line.decode(errors="replace").rstrip()
            if line:
                logger.debug("[gst] %s", line)

    @staticmethod
    def _gst_element_exists(element_name: str) -> bool:
        """Return True if a GStreamer element is available on this system."""
        try:
            result = subprocess.run(
                ["gst-inspect-1.0", "--exists", element_name],
                capture_output=True,
            )
            return result.returncode == 0
        except FileNotFoundError:
            return False
