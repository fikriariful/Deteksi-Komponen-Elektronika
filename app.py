# =============================================================================
# app.py — Backend Server Sistem Deteksi Komponen Elektronika (ElectroCom61)
# =============================================================================
# Arsitektur Khusus: Raspberry Pi 4 + Raspberry Pi Camera Module (Rev 1.3 / CSI)
# 
# Alur Pipeline:
# 1. Background thread (CameraStream) membaca frame sensor Raspberry Pi Camera
#    secara native via Picamera2 (atau fallback OpenCV jika diuji di PC/laptop).
# 2. Frame video diproses model YOLOv8n (ONNX Runtime CPU) setiap FRAME_SKIP frame.
# 3. Posisi titik tengah bounding box diuji terhadap Zona Inspeksi (ROI tengah).
# 4. Objek dalam ROI dilacak pada dictionary `active_tracking` dengan confidence tertinggi.
# 5. Saat objek keluar dari ROI melebihi toleransi EXIT_DELAY (1.0 detik), data disimpan ke SQLite.
# 6. Video di-stream ke browser via MJPEG multipart/x-mixed-replace.
# 7. Telemetri (latensi, hasil deteksi, log) di-push secara real-time via Server-Sent Events (SSE).
# =============================================================================

import csv
import io
import json
import os
import queue
import sqlite3
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

# Sembunyikan pesan log V4L2 internal OpenCV yang tidak relevan di Raspberry Pi
os.environ["OPENCV_LOG_LEVEL"] = "FATAL"
os.environ["OPENCV_VIDEOIO_V4L2_DEBUG"] = "0"

import cv2
import numpy as np
from flask import Flask, Response, jsonify, render_template, request, send_file

try:
    cv2.setLogLevel(0)
except Exception:
    pass

from ultralytics import YOLO

# Modul Picamera2 untuk komunikasi langsung ke sensor Pi Camera Rev 1.3 via libcamera
try:
    from picamera2 import Picamera2
    PICAMERA2_ADA = True
except ImportError:
    sys.path.append("/usr/lib/python3/dist-packages")
    try:
        from picamera2 import Picamera2
        PICAMERA2_ADA = True
    except ImportError:
        PICAMERA2_ADA = False

# =============================================================================
# KONFIGURASI SISTEM & PARAMETER OPERASIONAL
# =============================================================================
MODEL_PATH   = os.getenv("MODEL_PATH", "models/best.onnx")
IMGSZ        = int(os.getenv("IMGSZ", "320"))      # Resolusi inferensi optimal Raspberry Pi 4 (320x320)
CONF         = float(os.getenv("CONF", "0.60"))    # Ambang batas confidence default (60%)
PORT         = int(os.getenv("PORT", "5000"))
FRAME_SKIP   = int(os.getenv("FRAME_SKIP", "3"))   # Inferensi hanya tiap 3 frame (hemat 66% beban CPU)
CAM_WIDTH    = int(os.getenv("CAM_WIDTH",  "640")) # Lebar frame kamera (640 piksel)
CAM_HEIGHT   = int(os.getenv("CAM_HEIGHT", "480")) # Tinggi frame kamera (480 piksel)
JPEG_QUALITY = int(os.getenv("JPEG_QUALITY", "65"))# Kualitas kompresi stream video web
EXIT_DELAY   = 1.0                                 # Toleransi waktu objek keluar sebelum dicatat (1.0 detik)
ROI          = (0.25, 0.75, 0.1, 0.9)              # Batas Zona Inspeksi tengah (xmin, xmax, ymin, ymax)

# =============================================================================
# INISIALISASI MODEL YOLO (ONNX RUNTIME)
# =============================================================================
model = YOLO(MODEL_PATH)
model_lock = threading.Lock()

# Warmup run: Alokasi memory pool ONNX Runtime agar frame pertama tidak mengalami lag
model.predict(np.zeros((IMGSZ, IMGSZ, 3), dtype=np.uint8), imgsz=IMGSZ, verbose=False)

# =============================================================================
# MANAJEMEN DATABASE SQLITE
# =============================================================================
DB_PATH = Path("data/history.db")
DB_PATH.parent.mkdir(exist_ok=True)

def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS deteksi (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                waktu      TEXT    NOT NULL,
                kelas      TEXT    NOT NULL,
                confidence REAL    NOT NULL
            )
        """)
        conn.commit()

init_db()

# =============================================================================
# STATE GLOBAL & SINKRONISASI THREAD
# =============================================================================
state_lock    = threading.Lock()
latest_frame  = None
latest_result = {"detections": [], "ms": 0, "ts": 0, "running": False}
current_conf  = CONF
is_running    = False

# active_tracking: Menyimpan komponen yang sedang melintas di Zona Inspeksi
# Format: {kelas: {"last_seen": unix_timestamp, "conf": max_confidence}}
active_tracking: dict[str, dict] = {}
tracking_lock = threading.Lock()

# Antrean client Server-Sent Events (SSE)
sse_clients: list[queue.Queue] = []
sse_lock = threading.Lock()

app = Flask(__name__)

# =============================================================================
# FUNGSI PEMROSESAN CITRA & LOGIKA TRACKING
# =============================================================================

def push_sse(data: dict):
    """Mendistribusikan data telemetri deteksi ke seluruh browser via SSE."""
    payload = "data: " + json.dumps(data, ensure_ascii=False) + "\n\n"
    dead = []
    with sse_lock:
        for q in sse_clients:
            try:
                q.put_nowait(payload)
            except queue.Full:
                dead.append(q)
        for q in dead:
            sse_clients.remove(q)


def draw_boxes_on_frame(img_bgr: np.ndarray, detections: list) -> bytes:
    """
    Menggambar visualisasi inspeksi pada frame:
    1. Kotak kuning pembatas Zona Inspeksi (ROI).
    2. Bounding box berwarna cerah untuk komponen di DALAM zona inspeksi.
    3. Bounding box abu-abu untuk komponen di LUAR zona inspeksi.
    4. Label nama komponen dan persentase nilai confidence.
    """
    h, w = img_bgr.shape[:2]

    COLORS = [
        (0, 255, 136), (0, 212, 255), (255, 149, 0),
        (199, 125, 255), (255, 59, 59), (255, 209, 102)
    ]

    # Gambar batas Zona Inspeksi (kuning)
    zx1, zx2 = int(ROI[0] * w), int(ROI[1] * w)
    zy1, zy2 = int(ROI[2] * h), int(ROI[3] * h)
    cv2.rectangle(img_bgr, (zx1, zy1), (zx2, zy2), (0, 255, 255), 2)
    cv2.putText(img_bgr, "ZONA INSPEKSI", (zx1 + 5, zy1 + 20),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)

    for i, d in enumerate(detections):
        x1n, y1n, x2n, y2n = d["box"]
        cxn = (x1n + x2n) / 2.0
        cyn = (y1n + y2n) / 2.0

        in_zone = (ROI[0] <= cxn <= ROI[1]) and (ROI[2] <= cyn <= ROI[3])

        x1, y1 = int(x1n * w), int(y1n * h)
        x2, y2 = int(x2n * w), int(y2n * h)

        if in_zone:
            color = COLORS[i % len(COLORS)]
            lw = max(2, w // 350)
            text_color = (0, 0, 0)
        else:
            color = (150, 150, 150)
            lw = 1
            text_color = (255, 255, 255)

        cv2.rectangle(img_bgr, (x1, y1), (x2, y2), color, lw)

        if in_zone:
            cs = max(10, min(x2 - x1, y2 - y1) // 5)
            clw = max(3, lw + 1)
            for (cx, cy), dx, dy in [
                ((x1, y1), 1, 1), ((x2, y1), -1, 1),
                ((x1, y2), 1, -1), ((x2, y2), -1, -1)
            ]:
                cv2.line(img_bgr, (cx, cy), (cx + dx * cs, cy), color, clw)
                cv2.line(img_bgr, (cx, cy), (cx, cy + dy * cs), color, clw)

        label = f"{d['kelas']}  {int(d['confidence'] * 100)}%"
        fscale = max(0.4, w / 1200)
        fthick = max(1, w // 600)
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, fscale, fthick)
        ly = max(y1, th + 6)
        cv2.rectangle(img_bgr, (x1, ly - th - 6), (x1 + tw + 8, ly), color, -1)
        cv2.putText(img_bgr, label, (x1 + 4, ly - 4),
                    cv2.FONT_HERSHEY_SIMPLEX, fscale, text_color, fthick, cv2.LINE_AA)

    ok, buf = cv2.imencode(".jpg", img_bgr, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    return buf.tobytes() if ok else b""


def process_tracking(detections: list) -> list:
    """
    Logika Debouncing: Menyimpan komponen ke database SQLite tepat 1 kali.
    - Saat komponen berada di dalam ROI: simpan confidence tertinggi.
    - Saat komponen keluar dari ROI dan hilang > EXIT_DELAY (1.0 detik): simpan ke database.
    """
    now = time.time()
    newly_saved = []

    with tracking_lock:
        # Update status komponen yang ada di dalam Zona Inspeksi
        for d in detections:
            cx = (d["box"][0] + d["box"][2]) / 2.0
            cy = (d["box"][1] + d["box"][3]) / 2.0

            if ROI[0] <= cx <= ROI[1] and ROI[2] <= cy <= ROI[3]:
                kelas = d["kelas"]
                if kelas not in active_tracking:
                    active_tracking[kelas] = {"last_seen": now, "conf": d["confidence"]}
                else:
                    active_tracking[kelas]["last_seen"] = now
                    if d["confidence"] > active_tracking[kelas]["conf"]:
                        active_tracking[kelas]["conf"] = d["confidence"]

        # Catat komponen yang sudah keluar dari zona inspeksi melebihi EXIT_DELAY
        for kelas in list(active_tracking.keys()):
            data = active_tracking[kelas]
            if now - data["last_seen"] > EXIT_DELAY:
                waktu_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                with get_db() as conn:
                    conn.execute(
                        "INSERT INTO deteksi (waktu, kelas, confidence) VALUES (?,?,?)",
                        (waktu_str, kelas, round(data["conf"], 3))
                    )
                    conn.commit()

                newly_saved.append({
                    "waktu":      waktu_str,
                    "kelas":      kelas,
                    "confidence": round(data["conf"] * 100, 1)
                })
                del active_tracking[kelas]

    return newly_saved

# =============================================================================
# KELAS KAMERA KHUSUS RASPBERRY PI (THREADED BUFFERLESS)
# =============================================================================

class CameraStream:
    """
    Threaded capture untuk hardware Raspberry Pi Camera Module Rev 1.3 (CSI).
    Menjalankan background thread polling agar stream bebas buffer delay.
    Jika Picamera2 tidak tersedia (misal diuji di PC), otomatis fallback ke OpenCV.
    """
    def __init__(self):
        self.stopped = False
        self.picam   = None
        self.stream  = None
        self.ret     = False
        self.frame   = None

        # Prioritas 1: Raspberry Pi Camera Module Rev 1.3 via modul Picamera2
        if PICAMERA2_ADA:
            try:
                self.picam = Picamera2(0)
                config = self.picam.create_preview_configuration(
                    main={"size": (CAM_WIDTH, CAM_HEIGHT), "format": "RGB888"}
                )
                self.picam.configure(config)
                self.picam.start()
                time.sleep(0.2)
                self.frame = self.picam.capture_array()
                self.ret   = self.frame is not None
                print("Kamera: Terhubung via Raspberry Pi Camera (Picamera2 CSI)")
            except Exception as e:
                print(f"Picamera2 gagal diinisialisasi ({e}), mencoba fallback OpenCV...")
                self.picam = None

        # Prioritas 2: Fallback OpenCV (jika dijalankan pada PC/Laptop pengujian)
        if self.picam is None:
            backend = cv2.CAP_V4L2 if sys.platform != "win32" else cv2.CAP_ANY
            cap = cv2.VideoCapture(0, backend)
            if not cap.isOpened() and backend != cv2.CAP_ANY:
                cap = cv2.VideoCapture(0)

            if cap.isOpened():
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAM_WIDTH)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAM_HEIGHT)
                for _ in range(15):
                    ret, frame = cap.read()
                    if ret and frame is not None and frame.size > 0:
                        self.stream = cap
                        self.ret    = True
                        self.frame  = frame
                        print("Kamera: Terhubung via OpenCV VideoCapture (0)")
                        break
                    time.sleep(0.1)

        self.thread = threading.Thread(target=self.update, daemon=True)
        self.thread.start()

    def is_opened(self) -> bool:
        if self.picam is not None:
            return bool(self.ret)
        return self.stream is not None and self.stream.isOpened()

    def update(self):
        while not self.stopped:
            if self.picam is not None:
                try:
                    frame = self.picam.capture_array()
                except Exception:
                    time.sleep(0.01)
                    continue
                self.ret, self.frame = True, frame
            else:
                if self.stream is None or not self.stream.isOpened():
                    break
                ret, frame = self.stream.read()
                if ret:
                    self.ret, self.frame = ret, frame
                else:
                    time.sleep(0.01)

    def read(self):
        return self.ret, self.frame

    def stop(self):
        self.stopped = True
        self.thread.join(timeout=1.0)
        if self.picam is not None:
            try:
                self.picam.stop()
                self.picam.close()
            except Exception:
                pass
        elif self.stream is not None:
            self.stream.release()

# =============================================================================
# PIPELINE UTAMA DETEKSI AI (CAMERA LOOP)
# =============================================================================

def camera_loop():
    global latest_frame, latest_result, is_running

    try:
        cap = CameraStream()
    except Exception as e:
        print(f"Gagal menginisialisasi kamera: {e}")
        is_running = False
        return

    time.sleep(0.5)

    if not cap.is_opened():
        print("Hardware kamera tidak terdeteksi atau gagal dibuka.")
        cap.stop()
        is_running = False
        return

    frame_count      = 0
    last_deteksi     = []
    last_frame_bytes = None

    while is_running:
        ret, frame = cap.read()
        if not ret or frame is None:
            time.sleep(0.05)
            continue

        frame_count += 1

        # Pemrosesan inferensi YOLOv8n setiap FRAME_SKIP frame (Default = 3)
        if frame_count % FRAME_SKIP == 0:
            mulai = time.perf_counter()

            with model_lock:
                hasil = model.predict(
                    frame.copy(),
                    imgsz=IMGSZ,
                    conf=current_conf,
                    verbose=False
                )[0]

            ms = round((time.perf_counter() - mulai) * 1000)

            last_deteksi = [
                {
                    "kelas":      model.names[int(b.cls[0])],
                    "confidence": round(float(b.conf[0]), 3),
                    "box":        [round(float(v), 4) for v in b.xyxyn[0]],
                }
                for b in hasil.boxes
            ]

            newly_saved      = process_tracking(last_deteksi)
            last_frame_bytes = draw_boxes_on_frame(frame.copy(), last_deteksi)

            result_data = {
                "detections":  last_deteksi,
                "ms":          ms,
                "ts":          time.time(),
                "running":     True,
                "newly_saved": newly_saved
            }

            with state_lock:
                latest_frame  = last_frame_bytes
                latest_result = result_data

            push_sse(result_data)

        else:
            # Render frame perantara dengan hasil deteksi sebelumnya agar video tetap mulus
            if last_deteksi is not None:
                frame_bytes = draw_boxes_on_frame(frame.copy(), last_deteksi)
                with state_lock:
                    latest_frame = frame_bytes

        # Throttle 10ms menjaga kestabilan temperatur CPU Raspberry Pi 4
        time.sleep(0.01)

    cap.stop()
    with state_lock:
        latest_frame  = None
        latest_result = {
            "detections":  [],
            "ms":          0,
            "ts":          0,
            "running":     False,
            "newly_saved": []
        }
    push_sse(latest_result)

# =============================================================================
# ENDPOINT REST API & ROUTING FLASK
# =============================================================================

@app.get("/")
def index():
    return render_template("display.html")


@app.post("/api/start")
def start_cam():
    """Mengaktifkan pipeline kamera dan deteksi AI."""
    global is_running
    if not is_running:
        is_running = True
        threading.Thread(target=camera_loop, daemon=True).start()
    return jsonify(status="ok", running=True)


@app.post("/api/stop")
def stop_cam():
    """Menghentikan pipeline deteksi kamera."""
    global is_running
    is_running = False
    return jsonify(status="ok", running=False)


@app.post("/api/config")
def set_config():
    """Mengubah nilai ambang batas confidence secara dinamis dari web."""
    global current_conf
    try:
        current_conf = float(request.json.get("conf", current_conf))
    except (ValueError, TypeError):
        pass
    return jsonify(status="ok", conf=current_conf)


@app.get("/api/history")
def get_history():
    """Mengambil riwayat deteksi dari database SQLite."""
    limit = int(request.args.get("limit", 100))
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, waktu, kelas, confidence FROM deteksi ORDER BY id DESC LIMIT ?",
            (limit,)
        ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.delete("/api/history")
def clear_history():
    """Mengosongkan riwayat deteksi sebelum memulai sesi baru."""
    with get_db() as conn:
        conn.execute("DELETE FROM deteksi")
        conn.commit()
    with tracking_lock:
        active_tracking.clear()
    return jsonify(status="ok", message="Riwayat berhasil dihapus")


@app.get("/api/history/export")
def export_history():
    """Ekspor riwayat ke file CSV dengan encoding utf-8-sig (Excel compatible)."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, waktu, kelas, confidence FROM deteksi ORDER BY id ASC"
        ).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["No", "Waktu", "Komponen", "Confidence (%)"])
    for i, r in enumerate(rows, 1):
        writer.writerow([i, r["waktu"], r["kelas"], f"{r['confidence']*100:.1f}"])

    output.seek(0)
    buf      = io.BytesIO(output.getvalue().encode("utf-8-sig"))
    filename = f"riwayat_deteksi_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return send_file(buf, mimetype="text/csv", as_attachment=True, download_name=filename)


@app.get("/results/stream")
def results_stream():
    """Streaming data telemetri real-time via Server-Sent Events (SSE)."""
    def event_stream(q: queue.Queue):
        with state_lock:
            init = latest_result
        yield "data: " + json.dumps(init, ensure_ascii=False) + "\n\n"

        while True:
            try:
                yield q.get(timeout=15)
            except queue.Empty:
                yield ": keepalive\n\n"

    q: queue.Queue = queue.Queue(maxsize=10)
    with sse_lock:
        sse_clients.append(q)

    resp = Response(
        event_stream(q),
        mimetype="text/event-stream",
        headers={
            "Cache-Control":     "no-cache",
            "X-Accel-Buffering": "no"
        }
    )

    @resp.call_on_close
    def remove_client():
        with sse_lock:
            if q in sse_clients:
                sse_clients.remove(q)

    return resp


@app.get("/video/stream")
def video_stream():
    """Streaming video live via MJPEG multipart/x-mixed-replace."""
    def generate():
        while True:
            with state_lock:
                frame   = latest_frame
                running = is_running

            if not running:
                time.sleep(1)
                continue

            if frame:
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
                )

            time.sleep(0.04)

    return Response(generate(), mimetype="multipart/x-mixed-replace; boundary=frame")


# =============================================================================
# ENTRY POINT
# =============================================================================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, threaded=True)
