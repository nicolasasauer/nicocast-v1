# NicoCast v1

**Miracast-kompatibler Empfänger (Sink) für den Raspberry Pi Zero 2W**

NicoCast verwandelt einen Raspberry Pi Zero 2W in einen kabellosen
Bildschirmempfänger – ähnlich einem Chromecast oder Miracast-Dongle.
Android-Geräte (z. B. über Samsung Smart View) können sich über eine direkte
**Wi-Fi Direct (P2P)**-Verbindung verbinden – kein WLAN-Router notwendig.

Der Bildschirm wird über den integrierten **Mini-HDMI**-Ausgang ausgegeben.

---

## Funktionsweise

```
Android-Gerät
  ↕ Wi-Fi Direct (P2P)
Raspberry Pi Zero 2W
  ├─ wpa_supplicant (P2P Group Owner)
  ├─ NicoCast RTSP-Server (Port 7236)  ← Miracast-Handshake
  ├─ GStreamer-Pipeline  ← H.264 RTP-Stream empfangen & dekodieren
  └─ HDMI-Ausgang
```

1. Der Pi konfiguriert `wpa_supplicant` als P2P Group Owner und sendet
   **WFD Information Elements**, damit Android-Geräte ihn als Miracast-Sink erkennen.
2. Das Android-Gerät stellt eine Wi-Fi Direct-Verbindung her (z. B. über
   *Smart View* bei Samsung).
3. NicoCast verhandelt die Session über **RTSP** (M1–M12 gem. WFD-Spezifikation).
4. Der Videostream (H.264, RTP/UDP) wird mit Hardware-Dekodierung
   (`v4l2h264dec`) abgespielt und auf dem HDMI-Bildschirm ausgegeben.
5. Einstellungen können über das integrierte **Web-UI** geändert werden
   (erreichbar unter `http://<pi-ip>:8080/`).

---

## Voraussetzungen

### Hardware
- Raspberry Pi Zero 2W
- Micro-SD-Karte (≥ 8 GB)
- Mini-HDMI-auf-HDMI-Adapter + HDMI-Bildschirm
- Micro-USB-Netzteil (≥ 2,5 A empfohlen)

### Software
- **Raspberry Pi OS Bookworm** (64-bit empfohlen) oder Bullseye
- Internetverbindung für die Installation (danach nicht mehr nötig)

---

## Installation

```bash
# 1. Repository klonen
git clone https://github.com/nicolasasauer/nicocast-v1.git
cd nicocast-v1

# 2. Installationsskript ausführen (als root)
sudo bash scripts/install.sh
```

Das Skript:
- installiert alle Abhängigkeiten (`gstreamer`, `wpasupplicant`, `dnsmasq`, …)
- konfiguriert `wpa_supplicant` für Wi-Fi Direct P2P
- richtet einen systemd-Dienst ein, der beim Booten startet
- kopiert die Konfigurationsdatei nach `/etc/nicocast/nicocast.conf`

### Abhängigkeiten (werden automatisch installiert)

| Paket | Zweck |
|---|---|
| `wpasupplicant` | Wi-Fi Direct P2P |
| `dnsmasq` | DHCP für verbundene Geräte |
| `gstreamer1.0-*` | Video-/Audiodekodierung und -ausgabe |
| `gstreamer1.0-omx-rpi` | Hardware-H.264-Dekodierung (ältere RPi OS) |
| `python3`, `flask` | Hauptanwendung + Web-UI |

---

## Nutzung

### Dienst steuern

```bash
sudo systemctl status nicocast   # Status
sudo systemctl restart nicocast  # Neustart
sudo journalctl -fu nicocast     # Live-Logs
```

### Mit Android verbinden

1. Öffne auf deinem Android-Gerät **Einstellungen → Smart View** (Samsung)
   oder **Einstellungen → Verbindung → Wireless Display / Cast**.
2. NicoCast erscheint in der Geräteliste als **„NicoCast"** (anpassbar).
3. Tippe darauf – die Verbindung wird automatisch hergestellt (PBC-Modus).
4. Der Bildschirm wird auf dem HDMI-Display des Pi angezeigt.

---

## Einstellungen

### Web-UI

Öffne `http://<pi-ip>:8080/` im Browser.  
Dort kannst du Gerätename, Verbindungsmethode, Video-Ausgabe und mehr anpassen.

### Konfigurationsdatei

```ini
# /etc/nicocast/nicocast.conf

[general]
device_name = NicoCast          # Name auf dem Android-Gerät
pin = 12345678                  # WPS-PIN (nur bei connection_method = pin)
connection_method = pbc         # pbc (kein PIN) oder pin

[wifi]
interface = wlan0               # Netzwerkinterface
p2p_go_intent = 15              # 15 = immer Group Owner (kein Router nötig)
channel = 6                     # Wi-Fi-Kanal

[miracast]
rtsp_port = 7236                # RTSP-Kontrollport (WFD-Standard)
rtp_port = 1028                 # Eingehender RTP-Videoport
video_formats = 00 00 02 04 0000FFFF 00000000 00000000 00 0000 0000 00 none none

[display]
video_sink = auto               # auto | kmssink | fbdevsink | fakesink
fullscreen = true
audio_output = hdmi             # hdmi | headphone | disabled
hw_decode = true                # Hardware-H.264-Dekodierung

[webui]
enabled = true
port = 8080
```

Nach Änderungen in der Datei den Dienst neu starten:
```bash
sudo systemctl restart nicocast
```

---

## Projektstruktur

```
nicocast-v1/
├── nicocast/                   # Python-Paket (Hauptanwendung)
│   ├── __init__.py
│   ├── __main__.py             # python -m nicocast
│   ├── main.py                 # Einstiegspunkt & Orchestrierung
│   ├── config.py               # Konfigurationsverwaltung
│   ├── wifi_p2p.py             # Wi-Fi Direct P2P (wpa_supplicant)
│   ├── rtsp_handler.py         # Miracast RTSP-Protokoll (M1–M12)
│   ├── display_pipeline.py     # GStreamer Video-/Audiopipeline
│   ├── web_ui.py               # Flask-Web-UI
│   └── templates/
│       └── index.html          # Web-UI-Template
├── config/
│   └── nicocast.conf           # Standardkonfiguration
├── systemd/
│   └── nicocast.service        # systemd-Dienst
├── scripts/
│   ├── install.sh              # Installationsskript
│   └── setup_wpa_supplicant.sh # wpa_supplicant P2P-Konfiguration
├── requirements.txt
└── README.md
```

---

## Technische Details

### Wi-Fi Direct / WFD Advertisement

NicoCast setzt folgende `wfd_subelems` in `wpa_supplicant`:

```
000600111C440032
│ │ │   │   └── Max. Durchsatz: 50 Mbit/s (0x0032)
│ │ │   └────── RTSP-Port: 7236 (0x1C44)
│ │ └────────── WFD Device Info: Primary Sink + Session Available (0x0011)
│ └──────────── Datenlänge: 6 Byte
└────────────── Subelement-ID: 0
```

### RTSP-Ablauf (Miracast M1–M12)

```
Android  →  RPi   M1:  OPTIONS *
Android  ←  RPi   M2:  200 OK  (Public: …)
Android  →  RPi   M3:  GET_PARAMETER  (wfd_video_formats, …)
Android  ←  RPi   M4:  200 OK  (Fähigkeiten des Sinks)
Android  →  RPi   M5:  SET_PARAMETER  (gewählte Parameter)
Android  ←  RPi   M6:  200 OK
Android  →  RPi   M7:  SET_PARAMETER  (wfd_trigger_method: SETUP)
Android  ←  RPi   M8:  200 OK
Android  →  RPi   M9:  SETUP
Android  ←  RPi   M10: 200 OK  (Session-ID, RTP-Port)
Android  →  RPi   M11: PLAY
Android  ←  RPi   M12: 200 OK
               ↓
    RTP/H.264-Stream → UDP-Port 1028 → GStreamer → HDMI
```

### GStreamer-Pipeline

```
udpsrc port=1028
  → rtpjitterbuffer
  → rtph264depay
  → h264parse
  → v4l2h264dec  (Hardware-Dekodierung, RPi Zero 2W)
  → videoconvert
  → autovideosink / kmssink  (HDMI)
```

---

## Fehlerbehebung

| Problem | Lösung |
|---|---|
| Gerät erscheint nicht in Smart View | `sudo journalctl -fu nicocast` prüfen; sicherstellen dass `wpa_supplicant` läuft |
| Verbindung schlägt fehl | Verbindungsmethode auf `pbc` setzen; `wpa_cli -i wlan0 status` prüfen |
| Kein Bild | `video_sink = kmssink` ausprobieren; `gst-launch-1.0` manuell testen |
| Ruckeln / Artefakte | `jitter_buffer_ms` erhöhen; `video_formats` auf kleinere Auflösung setzen |
| Web-UI nicht erreichbar | `sudo systemctl status nicocast` & Port-Firewall prüfen |

---

## Lizenz

MIT – siehe [LICENSE](LICENSE).