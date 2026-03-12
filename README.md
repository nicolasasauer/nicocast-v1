# NicoCast v1

> **⚠️ Disclaimer:** Dieses Projekt wurde vollständig mit Hilfe von KI-Agenten (GitHub Copilot Coding Agent) erstellt. Der gesamte Code, die Dokumentation und die Konfigurationsdateien wurden durch KI generiert und können Fehler enthalten. Eine sorgfältige Prüfung vor dem produktiven Einsatz wird empfohlen.

**Miracast-kompatibler Empfänger (Sink) für den Raspberry Pi Zero 2W**

NicoCast verwandelt einen Raspberry Pi Zero 2W in einen kabellosen
Bildschirmempfänger – ähnlich einem Chromecast oder Miracast-Dongle.
Android-Geräte können sich über eine direkte **Wi-Fi Direct (P2P)**-Verbindung
verbinden. Kein WLAN-Router notwendig. Der Bildschirm wird über den
integrierten **Mini-HDMI**-Ausgang ausgegeben.

```
Android-Gerät  ←──── Wi-Fi Direct (P2P) ────→  Raspberry Pi Zero 2W
                                                         │
                                               Mini-HDMI-Ausgang
                                                         │
                                                  HDMI-Bildschirm
```

---

## Inhaltsverzeichnis

1. [Voraussetzungen](#1-voraussetzungen)
2. [SD-Karte flashen](#2-sd-karte-flashen)
3. [Erster Start & SSH-Verbindung](#3-erster-start--ssh-verbindung)
4. [NicoCast installieren](#4-nicocast-installieren)
5. [Verbindung herstellen (Android)](#5-verbindung-herstellen-android)
6. [Web-UI](#6-web-ui)
7. [Dienst verwalten](#7-dienst-verwalten)
   - [Betriebsmodi](#betriebsmodi)
8. [Konfigurationsreferenz](#8-konfigurationsreferenz)
9. [Projektstruktur](#9-projektstruktur)
10. [Technische Details](#10-technische-details)
11. [Fehlerbehebung](#11-fehlerbehebung)

---

## 1. Voraussetzungen

### Hardware

| Komponente | Hinweis |
|---|---|
| Raspberry Pi Zero 2W | Das einzige offiziell unterstützte Modell |
| Micro-SD-Karte ≥ 8 GB | Empfohlen: Class 10 / A1 |
| Mini-HDMI-auf-HDMI-Kabel oder -Adapter | Für die Bildschirmausgabe |
| HDMI-Bildschirm oder -Monitor | |
| Micro-USB-Netzteil ≥ 2,5 A | USB-Daten-Port für Strom (nicht OTG) |
| PC/Mac mit SD-Karten-Leser | Für die SD-Karten-Vorbereitung |

### Software

- **Raspberry Pi OS Lite 64-bit** (Bookworm) – empfohlen
  - Lite = kein Desktop, minimales Image, ideal für headless Betrieb
  - 64-bit = Voraussetzung für Hardware-H.264-Dekodierung via `v4l2h264dec`
- Raspberry Pi Imager ≥ 1.8 ([raspberrypi.com/software](https://www.raspberrypi.com/software/))

---

## 2. SD-Karte flashen

### 2.1  Raspberry Pi Imager öffnen

Starte den **Raspberry Pi Imager** auf deinem PC/Mac.

```
┌─────────────────────────────────────────────────────────────────┐
│                    Raspberry Pi Imager 1.8                       │
│                                                                  │
│  Gerät:       [  Raspberry Pi Zero 2W                       ▼]  │
│  Betriebssystem: [  Raspberry Pi OS Lite (64-bit)           ▼]  │
│  SD-Karte:    [  /dev/sdX (Ihre Karte)                      ▼]  │
│                                                                  │
│                         [ WEITER ]                              │
└─────────────────────────────────────────────────────────────────┘
```

**Einstellungen (Zahnrad-Symbol) konfigurieren:**

```
┌─────────────────────────────────────────────────────────────────┐
│  Erweiterte Optionen                                             │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ ☑  Hostname setzen:     raspberrypi                      │   │
│  │ ☑  SSH aktivieren:      ◉ Passwort-Authentifizierung     │   │
│  │ ☑  Benutzer:            pi   Passwort: ●●●●●●●●          │   │
│  │ ☑  WLAN konfigurieren:  SSID: MeinHeimnetz               │   │  ← optional, nur für
│  │                          PSK:  ●●●●●●●●●●                │   │    ersten SSH-Zugang
│  │    Land: DE                                               │   │
│  │ ☑  Locale:              Europe/Berlin  de                 │   │
│  └─────────────────────────────────────────────────────────┘   │
│                         [ SPEICHERN ]                            │
└─────────────────────────────────────────────────────────────────┘
```

> **Hinweis:** Das WLAN-Heimnetz wird nur für den ersten SSH-Zugang und die
> Installation benötigt. NicoCast läuft standardmäßig im **Hybrid-Modus**:
> NetworkManager bleibt aktiv, sodass die bestehende WLAN- und SSH-Verbindung
> erhalten bleibt. Das P2P-Interface für Miracast wird parallel dazu betrieben.
> Wer maximale Miracast-Performance benötigt, kann später auf den
> Performance-Modus umschalten (siehe [Betriebsmodi](#betriebsmodi)).

### 2.2  Image schreiben

Klicke auf **„Schreiben"** und bestätige. Der Vorgang dauert ca. 3–5 Minuten.

---

## 3. Erster Start & SSH-Verbindung

### 3.1  Raspberry Pi starten

1. SD-Karte in den Pi stecken
2. HDMI-Kabel anschließen (optional für Ersteinrichtung, aber empfohlen)
3. Netzteil einstecken – der Pi bootet automatisch

### 3.2  IP-Adresse herausfinden

Methode A – Router-Admin-Oberfläche: Unter verbundenen Geräten nach
`raspberrypi` suchen.

Methode B – von einem anderen Linux/Mac im gleichen WLAN:

```bash
ping -c 1 raspberrypi.local
# Ausgabe: PING raspberrypi.local (192.168.1.42) ...
```

### 3.3  Per SSH verbinden

```
user@pc:~$ ssh pi@raspberrypi.local

The authenticity of host 'raspberrypi.local (192.168.1.42)' can't be established.
ED25519 key fingerprint is SHA256:xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx.
Are you sure you want to continue connecting (yes/no/[fingerprint])? yes
Warning: Permanently added 'raspberrypi.local' (ED25519) to the list of known hosts.
pi@raspberrypi.local's password:

Linux raspberrypi 6.6.31+rpt-rpi-v8 #1 SMP PREEMPT Debian 1:6.6.31-1+rpt1 aarch64

The programs included with the Debian GNU/Linux system are free software;
...

pi@raspberrypi:~ $
```

### 3.4  System aktualisieren

```bash
pi@raspberrypi:~ $ sudo apt-get update && sudo apt-get upgrade -y
```

Das dauert beim ersten Mal ca. 5–10 Minuten.

---

## 4. NicoCast installieren

### 4.1  Repository klonen

Zuerst `git` installieren (falls noch nicht vorhanden):

```bash
pi@raspberrypi:~ $ sudo apt-get install -y git
```

Dann das Repository klonen:

```bash
pi@raspberrypi:~ $ git clone https://github.com/nicolasasauer/nicocast-v1.git
Cloning into 'nicocast-v1'...
remote: Enumerating objects: 42, done.
remote: Counting objects: 100% (42/42), done.
Receiving objects: 100% (42/42), 28.54 KiB | 1.23 MiB/s, done.

pi@raspberrypi:~ $ cd nicocast-v1
```

### 4.2  Installationsskript ausführen

```bash
pi@raspberrypi:~/nicocast-v1 $ sudo bash scripts/install.sh
```

**Erwartete Ausgabe (ca. 3–5 Minuten):**

```
[+] Detected architecture: aarch64
[+] Unblocking Wi-Fi radio…
[+] Installing system dependencies…
[+] Creating service user 'nicocast'…
[+] Installing NicoCast to /opt/nicocast…
[+] Creating Python virtual environment…
[+] Headless OS detected – setting video_sink = kmssink in config.
[+] Config written to /etc/nicocast/nicocast.conf – edit as needed.
[+] Ensuring operation_mode = hybrid so SSH stays connected after install…
[+] Configuring wpa_supplicant for Wi-Fi Direct P2P (country: DE)…
[+] Writing wpa_supplicant P2P config to /etc/wpa_supplicant/wpa_supplicant-p2p.conf (country: DE)…
[+] Backed up existing wpa_supplicant.conf
[+] Enabling wpa_supplicant for interface wlan0…
[+] wpa_supplicant control socket ready.
[+] P2P is active on wlan0.
[+] wpa_supplicant P2P setup complete.
[+] Disabling system-wide dnsmasq service…
[+] Installing systemd service…

[+] ════════════════════════════════════════════
[+]   NicoCast installed successfully!
[+] ════════════════════════════════════════════

  • Service status:  sudo systemctl status nicocast
  • Live logs:       sudo journalctl -fu nicocast
  • Settings web UI: http://192.168.1.42:8080/
  • Config file:     /etc/nicocast/nicocast.conf
  • Toggle mode:     sudo toggle-mode.sh  (hybrid <-> performance)

[!] NicoCast is running in HYBRID mode (NetworkManager stays active).
[!] Your SSH/Wi-Fi connection is preserved.
[!] For lower video latency, switch to performance mode once you no longer need SSH:
[!]   sudo toggle-mode.sh
[!] On your Android device, open Smart View (or any Miracast app)
[!] and look for 'NicoCast'.
```

### 4.3  Für andere Länder: WLAN-Regulierungsbereich anpassen

```bash
# Beispiel: Österreich
sudo WIFI_COUNTRY=AT bash scripts/install.sh

# Beispiel: USA
sudo WIFI_COUNTRY=US bash scripts/install.sh
```

### 4.4  Installation prüfen

```bash
pi@raspberrypi:~ $ sudo systemctl status nicocast
● nicocast.service - NicoCast – Miracast sink for Raspberry Pi Zero 2W
     Loaded: loaded (/etc/systemd/system/nicocast.service; enabled; preset: enabled)
     Active: active (running) since Wed 2025-01-15 14:23:07 CET; 42s ago
   Main PID: 1337 (python)
      Tasks: 8 (limit: 361)
        CPU: 1.234s
     CGroup: /system.slice/nicocast.service
             └─1337 /opt/nicocast/venv/bin/python -m nicocast ...
```

```bash
pi@raspberrypi:~ $ sudo journalctl -fu nicocast
Jan 15 14:23:07 raspberrypi nicocast[1337]: 14:23:07  INFO      nicocast.main  ═══════════════════════════════════════
Jan 15 14:23:07 raspberrypi nicocast[1337]: 14:23:07  INFO      nicocast.main    NicoCast starting – device: 'NicoCast'
Jan 15 14:23:07 raspberrypi nicocast[1337]: 14:23:07  INFO      nicocast.main  ═══════════════════════════════════════
Jan 15 14:23:07 raspberrypi nicocast[1337]: 14:23:07  INFO      nicocast.wifi_p2p  Starting Wi-Fi Direct P2P…
Jan 15 14:23:07 raspberrypi nicocast[1337]: 14:23:07  INFO      nicocast.wifi_p2p  Wi-Fi Direct P2P started, advertising as 'NicoCast'
Jan 15 14:23:08 raspberrypi nicocast[1337]: 14:23:08  INFO      nicocast.rtsp_handler  RTSP server listening on 0.0.0.0:7236
Jan 15 14:23:08 raspberrypi nicocast[1337]: 14:23:08  INFO      nicocast.web_ui  Web UI available at http://0.0.0.0:8080/
Jan 15 14:23:08 raspberrypi nicocast[1337]: 14:23:08  INFO      nicocast.main  Ready. Waiting for connections on RTSP port 7236 …
```

Der Dienst ist bereit – NicoCast wartet jetzt auf Verbindungen.

### 4.5  Abhängigkeiten (werden automatisch installiert)

| Paket | Zweck |
|---|---|
| `wpasupplicant` | Wi-Fi Direct P2P |
| `rfkill` | Wi-Fi-Radio entsperren |
| `dnsmasq` | DHCP für verbundene Geräte |
| `gstreamer1.0-plugins-good` | V4L2-Hardware-H.264-Dekodierung (`v4l2h264dec`) |
| `gstreamer1.0-plugins-bad` | `kmssink` für HDMI-Ausgabe |
| `gstreamer1.0-libav` | Software-H.264-Fallback (`avdec_h264`) |
| `python3`, `flask` | Hauptanwendung + Web-UI |

> **Hinweis zur 64-bit-Hardware-Dekodierung:**  
> Auf Raspberry Pi OS Lite **64-bit** (aarch64) wird H.264 über das
> V4L2 M2M-Interface `/dev/video10–/dev/video12` dekodiert
> (`v4l2h264dec`-Element in GStreamer). Das frühere OpenMAX-Paket
> `gstreamer1.0-omx-rpi` ist auf 64-bit nicht verfügbar und wird
> nicht installiert.

---

## 5. Verbindung herstellen (Android)

### 5.1  Samsung-Geräte (Smart View)

```
Einstellungen
  └── Verbundene Geräte  (oder "Verbindungen")
       └── Smart View  (oder "Drahtlose Anzeige")
            └── [Liste der verfügbaren Geräte]
                 └── NicoCast  ←── Antippen
```

### 5.2  Andere Android-Geräte (Wireless Display / Cast)

```
Einstellungen
  └── Anzeige  (oder "Verbindung & Freigabe")
       └── Kabellose Anzeige  (oder "Cast" / "Screen Mirroring")
            └── [NicoCast erscheint in der Liste]
                 └── Antippen – Verbindung wird automatisch hergestellt
```

### 5.3  Verbindungsablauf (Live-Log)

Beim Verbindungsaufbau erscheinen folgende Log-Einträge:

```
14:25:12  INFO  nicocast.wifi_p2p  P2P peer found: aa:bb:cc:dd:ee:ff – auto-accepting
14:25:13  INFO  nicocast.wifi_p2p  P2P group started on interface 'p2p-wlan0-0' (role: GO)
14:25:13  INFO  nicocast.wifi_p2p  Assigned 192.168.49.1 to p2p-wlan0-0
14:25:14  INFO  nicocast.wifi_p2p  Source device connected: aa:bb:cc:dd:ee:ff
14:25:15  INFO  nicocast.wifi_p2p  Source device IP: 192.168.49.2
14:25:15  INFO  nicocast.rtsp_handler  RTSP connection from ('192.168.49.2', 49152)
14:25:15  INFO  nicocast.rtsp_handler  WFD trigger: SETUP
14:25:15  INFO  nicocast.rtsp_handler  Streaming started! RTP video → port 1028
14:25:15  INFO  nicocast.main  Session start: rtp_port=1028
14:25:15  INFO  nicocast.display_pipeline  Launching GStreamer pipeline:
  udpsrc port=1028 buffer-size=524288 caps="application/x-rtp,..." !
  rtpjitterbuffer latency=200 !
  rtph264depay ! h264parse ! v4l2h264dec !
  videoconvert ! kmssink fullscreen=true sync=false
14:25:15  INFO  nicocast.display_pipeline  GStreamer pipeline started (PID 2048)
```

Das Android-Gerät zeigt jetzt seinen Bildschirm auf dem HDMI-Display.

---

## 6. Web-UI

Öffne im Browser: **`http://<pi-ip>:8080/`**

(Die IP-Adresse steht in der Ausgabe von `sudo systemctl status nicocast` oder
`hostname -I`.)

```
┌─────────────────────────────────────────────────────────────────┐
│  🎬 NicoCast                                                      │
│  Miracast-Sink für Raspberry Pi Zero 2W                         │
│                                                                  │
│  ┌── Status ────────────────────────────────────────────────┐   │
│  │  Verbindung   [ Verbunden ●]    Stream    [ Aktiv ● ]    │   │
│  │  Gerät-IP     192.168.49.2      RTSP-Port  7236          │   │
│  │  Gerät-MAC    aa:bb:cc:dd:ee:ff RTP-Port   1028          │   │
│  │  Gerätename   NicoCast                                   │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
│  ┌── Einstellungen ─────────────────────────────────────────┐   │
│  │  Gerätename        [NicoCast                          ]  │   │
│  │  Verbindungsmethode[Kein PIN (Push-Button / PBC)     ▼]  │   │
│  │  WPS-PIN           [12345678                          ]  │   │
│  │  Video-Ausgabe     [KMS/DRM (empfohlen für RPi)      ▼]  │   │
│  │  Audio-Ausgabe     [HDMI                             ▼]  │   │
│  │  Log-Level         [INFO                             ▼]  │   │
│  │                    [💾 Speichern]                        │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
│  ┌── Dienst ────────────────────────────────────────────────┐   │
│  │  [🔄 Dienst neu starten]                                 │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
│  NicoCast v1.0  ·  Raspberry Pi Zero 2W                         │
└─────────────────────────────────────────────────────────────────┘
```

### API-Endpunkt

```bash
curl http://<pi-ip>:8080/api/status
# Ausgabe:
{
  "connected": true,
  "streaming": true,
  "peer_ip": "192.168.49.2",
  "peer_mac": "aa:bb:cc:dd:ee:ff",
  "group_iface": "p2p-wlan0-0",
  "my_ip": "192.168.49.1",
  "device_name": "NicoCast"
}
```

---

## 7. Dienst verwalten

```bash
# Status
sudo systemctl status nicocast

# Live-Logs (Strg+C zum Beenden)
sudo journalctl -fu nicocast

# Neustart (z. B. nach Konfigurationsänderung)
sudo systemctl restart nicocast

# Stoppen
sudo systemctl stop nicocast

# Beim Booten deaktivieren
sudo systemctl disable nicocast
```

### Betriebsmodi

NicoCast kennt zwei Betriebsmodi, die du mit `toggle-mode.sh` umschalten kannst:

| Modus | Verhalten | Empfehlung |
|---|---|---|
| **hybrid** | NetworkManager läuft weiter; bestehende WLAN- und SSH-Verbindung bleibt erhalten. | Standard nach der Installation – ideal für Einrichtung und Wartung. |
| **performance** | NetworkManager wird beim Start gestoppt; wpa_supplicant hat exklusive Kontrolle über `wlan0`. | Niedrigste Latenz, aber SSH-Verbindung geht verloren. |

> **Nach der Installation ist NicoCast immer im Hybrid-Modus.**
> Auf performance umschalten, sobald SSH nicht mehr benötigt wird:

```bash
# Zwischen hybrid und performance umschalten:
sudo toggle-mode.sh

# Aktuellen Modus prüfen:
grep operation_mode /etc/nicocast/nicocast.conf
```

Oder direkt in der Konfiguration setzen:

```bash
# Hybrid-Modus (SSH bleibt aktiv):
sudo sed -i 's/^operation_mode = .*/operation_mode = hybrid/' /etc/nicocast/nicocast.conf
sudo systemctl restart nicocast

# Performance-Modus (niedrigste Latenz, SSH trennt sich):
sudo sed -i 's/^operation_mode = .*/operation_mode = performance/' /etc/nicocast/nicocast.conf
sudo systemctl restart nicocast
```

---

## 8. Konfigurationsreferenz

Die Konfigurationsdatei liegt unter `/etc/nicocast/nicocast.conf`.
Nach Änderungen muss der Dienst neu gestartet werden:

```bash
sudo systemctl restart nicocast
```

```ini
[general]
device_name = NicoCast        # Name auf dem Android-Gerät (max. 32 Zeichen)
pin = 12345678                # WPS-PIN (nur bei connection_method = pin)
connection_method = pbc       # pbc (kein PIN) oder pin
log_level = INFO              # DEBUG | INFO | WARNING | ERROR
operation_mode = hybrid       # hybrid (SSH-sicher) | performance (niedrigste Latenz)

[wifi]
interface = wlan0             # Wireless-Interface (normalerweise wlan0)
p2p_go_intent = 15            # 15 = Pi immer Group Owner (kein Router nötig)
channel = 6                   # Wi-Fi-Kanal (2,4 GHz: 1–13)

[miracast]
rtsp_port = 7236              # RTSP-Kontrollport (WFD-Standard)
rtp_port = 1028               # Eingehender RTP-Videoport
audio_rtp_port = 1030         # Eingehender RTP-Audioport (0 = deaktiviert)
jitter_buffer_ms = 200        # Jitter-Buffer in ms (erhöhen bei Ruckeln)

[display]
video_sink = kmssink          # kmssink (RPi OS Lite) | auto | fakesink
fullscreen = true             # Vollbildmodus
audio_output = hdmi           # hdmi | headphone | disabled
hw_decode = true              # Hardware-H.264-Dekodierung (v4l2h264dec)

[webui]
enabled = true
port = 8080
bind = 0.0.0.0
```

---

## 9. Projektstruktur

```
nicocast-v1/
├── nicocast/                   # Python-Paket (Hauptanwendung)
│   ├── main.py                 # Einstiegspunkt & Orchestrierung
│   ├── config.py               # Konfigurationsverwaltung
│   ├── wifi_p2p.py             # Wi-Fi Direct P2P (wpa_supplicant)
│   ├── rtsp_handler.py         # Miracast RTSP-Protokoll (M1–M12)
│   ├── display_pipeline.py     # GStreamer Video-/Audiopipeline
│   ├── web_ui.py               # Flask-Web-UI
│   └── templates/index.html    # Web-UI-Template
├── config/
│   └── nicocast.conf           # Standardkonfiguration
├── systemd/
│   └── nicocast.service        # systemd-Dienst
├── scripts/
│   ├── install.sh              # Installationsskript
│   └── setup_wpa_supplicant.sh # wpa_supplicant P2P-Konfiguration
├── tests/                      # Unit-Tests (pytest)
├── requirements.txt
└── setup.py
```

---

## 10. Technische Details

### Systemarchitektur

```
Android-Gerät
  │
  │  Wi-Fi Direct (P2P) – 2,4 GHz, Kanal 6
  │
Raspberry Pi Zero 2W
  ├─ wpa_supplicant (P2P Group Owner, Interface: p2p-wlan0-0)
  │    └─ WFD Subelement: 000600111C440032
  │         (Primary Sink, Port 7236, 50 Mbps)
  │
  ├─ dnsmasq (DHCP, 192.168.49.2–254)
  │
  ├─ NicoCast RTSP-Server (TCP, Port 7236)  ← Miracast-Handshake
  │
  ├─ GStreamer-Pipeline
  │    udpsrc port=1028
  │      → rtpjitterbuffer (200 ms)
  │      → rtph264depay
  │      → h264parse
  │      → v4l2h264dec  (Hardware-Dekodierung via V4L2 M2M)
  │      → videoconvert
  │      → kmssink       (KMS/DRM → HDMI)
  │
  └─ Flask-Web-UI (Port 8080)
```

### Wi-Fi Direct / WFD Advertisement

```
WFD Subelement (Hex): 000600111C440032
│  │  │   │   └── Max. Durchsatz: 50 Mbit/s (0x0032)
│  │  │   └────── RTSP-Port: 7236 (0x1C44)
│  │  └────────── WFD Device Info: Primary Sink + Session Available (0x0011)
│  └──────────── Datenlänge: 6 Byte
└────────────── Subelement-ID: 0
```

### RTSP-Ablauf (Miracast M1–M12)

```
Android  →  RPi   M1:  OPTIONS *
Android  ←  RPi   M2:  200 OK  (Public: org.wfa.wfd1.0, GET_PARAMETER, …)
Android  →  RPi   M3:  GET_PARAMETER  (wfd_video_formats, wfd_audio_codecs, …)
Android  ←  RPi   M4:  200 OK  (Fähigkeiten: kmssink, v4l2h264dec, port 1028)
Android  →  RPi   M5:  SET_PARAMETER  (gewählte Parameter)
Android  ←  RPi   M6:  200 OK
Android  →  RPi   M7:  SET_PARAMETER  (wfd_trigger_method: SETUP)
Android  ←  RPi   M8:  200 OK
Android  →  RPi   M9:  SETUP         (Transport: RTP/AVP/UDP;unicast)
Android  ←  RPi   M10: 200 OK        (Session-ID, client_port=1028)
Android  →  RPi   M11: PLAY
Android  ←  RPi   M12: 200 OK
                 ↓
    RTP/H.264-Pakete → UDP 1028 → GStreamer → v4l2h264dec → kmssink → HDMI
```

### GStreamer-Pipeline (vollständig)

**Video (Standard):**
```
udpsrc port=1028 buffer-size=524288 caps="application/x-rtp,media=video,clock-rate=90000,encoding-name=H264,payload=33"
  ! rtpjitterbuffer latency=200
  ! rtph264depay
  ! h264parse
  ! v4l2h264dec          ← Hardware-Dekodierung (RPi Zero 2W, 64-bit Bookworm)
  ! videoconvert
  ! kmssink fullscreen=true sync=false
```

**Audio (AAC, parallel):**
```
udpsrc port=1030 caps="application/x-rtp,media=audio,clock-rate=44100,encoding-name=MPEG4-GENERIC"
  ! rtpjitterbuffer
  ! rtpmp4adepay ! aacparse ! avdec_aac
  ! audioconvert ! audioresample
  ! autoaudiosink
```

---

## 11. Fehlerbehebung

### NicoCast erscheint nicht in Smart View

```bash
# 1. Dienst-Status prüfen
sudo systemctl status nicocast

# 2. wpa_supplicant-Status prüfen
wpa_cli -i wlan0 status

# 3. P2P-Discovery prüfen
wpa_cli -i wlan0 p2p_find
wpa_cli -i wlan0 p2p_peers

# 4. WFD-Subelement prüfen
wpa_cli -i wlan0 get wfd_subelems
# Erwartete Ausgabe: 000600111C440032
```

### Verbindung schlägt fehl

```bash
# Auf PBC (kein PIN) umstellen
sudo sed -i 's/connection_method = pin/connection_method = pbc/' \
    /etc/nicocast/nicocast.conf
sudo systemctl restart nicocast
```

### Kein Bild auf dem HDMI-Bildschirm

```bash
# Video-Sink manuell testen
gst-launch-1.0 videotestsrc ! kmssink

# Falls kmssink nicht funktioniert (älterer Kernel):
sudo sed -i 's/video_sink = kmssink/video_sink = fbdevsink/' \
    /etc/nicocast/nicocast.conf
sudo systemctl restart nicocast

# GStreamer-Elemente prüfen
gst-inspect-1.0 v4l2h264dec   # Hardware-Dekoder vorhanden?
gst-inspect-1.0 kmssink        # KMS-Sink vorhanden?
ls /dev/video*                  # V4L2-Geräte vorhanden?
```

### Ruckeln oder Artefakte

```bash
# Jitter-Buffer erhöhen (in nicocast.conf)
sudo nano /etc/nicocast/nicocast.conf
# jitter_buffer_ms = 400   ← höherer Wert reduziert Ruckeln

# Video-Auflösung begrenzen (in nicocast.conf):
# video_formats = 00 00 02 04 0000003F 00000000 00000000 00 0000 0000 00 none none
# (0x3F = 1280×720@30fps statt @60fps)
```

### Hardware-Dekodierung schlägt fehl

```bash
# V4L2-Gerätedateien prüfen
ls -la /dev/video*
# Erwartete Ausgabe auf RPi Zero 2W mit Bookworm 64-bit:
# /dev/video10  /dev/video11  /dev/video12  /dev/video13  /dev/video14  /dev/video15  /dev/video16  /dev/video18  /dev/video19  /dev/video20  /dev/video21  /dev/video31

# v4l2 H.264 Decoder direkt testen
gst-inspect-1.0 v4l2h264dec
# Falls nicht vorhanden, Software-Dekodierung aktivieren:
sudo sed -i 's/hw_decode = true/hw_decode = false/' /etc/nicocast/nicocast.conf
sudo systemctl restart nicocast
```

### Web-UI nicht erreichbar

```bash
# Port 8080 prüfen
ss -tlnp | grep 8080

# Dienst neu starten
sudo systemctl restart nicocast

# IP-Adresse des Pi herausfinden
hostname -I
```

### Vollständige Zurücksetzung

```bash
sudo systemctl stop nicocast
sudo systemctl disable nicocast
sudo rm -rf /opt/nicocast
sudo rm -f /etc/systemd/system/nicocast.service
sudo rm -rf /etc/nicocast
sudo systemctl daemon-reload
```

---

## Lizenz

MIT – siehe [LICENSE](LICENSE).
