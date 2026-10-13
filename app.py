# =============================================================================
# app.py — Backend Server Sistem Deteksi Komponen Elektronika (ElectroCom61)
# =============================================================================
# Alur Sistem:
# 1. Thread kamera membaca frame video dan menyimpannya di memory buffer (NumPy array).
# 2. Frame diproses oleh model YOLOv8n (ONNX Runtime) setiap N frame (frame skipping).
# 3. Titik tengah bounding box dihitung untuk menentukan apakah objek berada dalam ROI.
# 4. Objek dalam ROI dilacak pada dictionary `active_tracking`.
# 5. Ketika objek tidak terdeteksi lagi melebihi batas EXIT_DELAY, data disimpan ke SQLite.
# 6. Stream video dikirim ke client via HTTP multipart JPEG (MJPEG).
# 7. Data deteksi (JSON) di-push ke client secara real-time via Server-Sent Events (SSE).
# =============================================================================

import csv
import glob
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

# Set level logging V4L2 pada OpenCV ke level FATAL agar pesan peringatan ioctl 
# dari node non-capture /dev/video tidak dicetak ke stdout/stderr.
os.environ["OPENCV_LOG_LEVEL"] = "FATAL"
os.environ["OPENCV_VIDEOIO_V4L2_DEBUG"] = "0"

import cv2
import numpy as np
from flask import Flask, Response, jsonify, render_template, request, send_file

# Set log level OpenCV Python binding ke 0 (silent/mati).
try:
    cv2.setLogLevel(0)
except Exception:
    pass

from ultralytics import YOLO

# Import Picamera2 untuk membaca sensor kamera Raspberry Pi via antarmuka libcamera.
# Jika dijalankan di dalam venv, tambahkan path sistem Linux agar modul picamera2 terbaca.
# Jika dijalankan di Windows/PC tanpa modul kamera Pi, PICAMERA2_ADA diset ke False.
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
# KONFIGURASI SISTEM
# =============================================================================
# MODEL_PATH   : Path menuju file bobot ONNX hasil export model YOLOv8n.
# IMGSZ        : Dimensi lebar dan tinggi input tensor untuk model (320x320 piksel).
# CONF         : Ambang batas confidence score minimum (0.0 sampai 1.0) untuk deteksi valid.
# PORT         : Nomor port TCP untuk binding HTTP server Flask.
# FRAME_SKIP   : Interval pemrosesan inferensi; model hanya dipanggil setiap N frame.
# CAM_WIDTH    : Lebar resolusi frame yang diminta ke hardware kamera (piksel).
# CAM_HEIGHT   : Tinggi resolusi frame yang diminta ke hardware kamera (piksel).
# JPEG_QUALITY : Tingkat kompresi JPEG untuk encoding frame video (rentang 0-100).
# CAM_BACKEND  : Penentu backend kamera ("auto", "picamera2", atau "opencv").
# EXIT_DELAY   : Durasi toleransi (detik) hilangnya objek sebelum dicatat ke database.
# ROI          : Koordinat batas Region of Interest (x_min, x_max, y_min, y_max) dalam skala 0.0 - 1.0.
MODEL_PATH   = os.getenv("MODEL_PATH", "models/best.onnx")
IMGSZ        = int(os.getenv("IMGSZ", "320"))
CONF         = float(os.getenv("CONF", "0.50"))
PORT         = int(os.getenv("PORT", "5000"))
FRAME_SKIP   = int(os.getenv("FRAME_SKIP", "3"))
CAM_WIDTH    = int(os.getenv("CAM_WIDTH",  "640"))
CAM_HEIGHT   = int(os.getenv("CAM_HEIGHT", "480"))
JPEG_QUALITY = int(os.getenv("JPEG_QUALITY", "65"))
CAM_BACKEND  = os.getenv("CAM_BACKEND", "auto")
EXIT_DELAY   = 1.0
ROI          = (0.25, 0.75, 0.1, 0.9)

# =============================================================================
# INISIALISASI MODEL YOLO (ONNX RUNTIME)
# =============================================================================
# Load arsitektur model dan bobot ONNX ke dalam memory.
model = YOLO(MODEL_PATH)

# Mutex lock untuk mencegah panggilan bersamaan (race condition) pada model.predict.
model_lock = threading.Lock()

# Warmup run: Melakukan satu kali inferensi menggunakan array kosong (zeros)
# untuk menginisialisasi alokasi memori ONNX Runtime sebelum loop kamera dimulai.
model.predict(np.zeros((IMGSZ, IMGSZ, 3), dtype=np.uint8), imgsz=IMGSZ, verbose=False)

# =============================================================================
# MANAJEMEN DATABASE SQLITE
# =============================================================================
DB_PATH = Path("data/history.db")
DB_PATH.parent.mkdir(exist_ok=True)

def get_db():
    """
    Membuka koneksi baru ke SQLite database.
    row_factory diset ke sqlite3.Row agar record dapat diakses menggunakan nama kolom (dict-like).
    """
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """
    Inisialisasi skema tabel jika belum tersedia.
    Tabel 'deteksi':
      - id         : Primary key integer increment.
      - waktu      : Timestamp penyimpanan format YYYY-MM-DD HH:MM:SS.
      - kelas      : Nama label kelas komponen elektronika.
      - confidence : Nilai confidence score float (0.000 - 1.000).
    """
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
# STATE GLOBAL & SINKRONISASI MULTI-THREAD
# =============================================================================
# state_lock    : Mutex untuk melindungi akses ke variabel `latest_frame` dan `latest_result`.
# latest_frame  : Menyimpan byte JPEG terakhir hasil encoding untuk stream video.
# latest_result : Dictionary payload data deteksi terakhir untuk SSE client.
# current_conf  : Nilai confidence threshold aktif yang dapat diubah via REST API.
# is_running    : Flag boolean pengontrol loop pada background thread kamera.
# camera_index  : Indeks perangkat kamera integer atau string URL IP camera.
state_lock    = threading.Lock()
latest_frame  = None
latest_result = {"detections": [], "ms": 0, "ts": 0, "running": False}
current_conf  = CONF
is_running    = False
camera_index  = int(os.getenv("CAM_INDEX", "0"))

# active_tracking: Menyimpan status objek dalam ROI.
# Struktur: {kelas_komponen: {"last_seen": unix_timestamp, "conf": max_confidence_score}}
active_tracking: dict[str, dict] = {}
tracking_lock = threading.Lock()

# sse_clients: Menyimpan list instance queue.Queue untuk setiap koneksi SSE yang aktif.
# sse_lock   : Mutex untuk melindungi operasi tambah/hapus queue pada `sse_clients`.
sse_clients: list[queue.Queue] = []
sse_lock = threading.Lock()

app = Flask(__name__)

# =============================================================================
# FUNGSI PEMROSESAN CITRA & LOGIKA TRACKING
# =============================================================================

def push_sse(data: dict):
    """
    Mendistribusikan data deteksi terbaru ke seluruh antrean client SSE yang terdaftar.
    Format pesan mengikuti spesifikasi text/event-stream: "data: <json_string>\n\n".
    Jika antrean client penuh (queue.Full), client dianggap terputus dan dihapus dari list.
    """
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
    Menggambar batas ROI dan bounding box hasil deteksi pada frame NumPy array BGR.
    
    Langkah:
    1. Menggambar persegi panjang ROI warna kuning (0, 255, 255) sesuai batas koordinat `ROI`.
    2. Menghitung titik tengah (center-x, center-y) dari setiap bounding box deteksi.
    3. Jika titik tengah berada di dalam batas ROI:
       - Bounding box digambar dengan warna aksen dari list `COLORS`.
       - Menambahkan aksen garis siku pada tiap sudut box.
    4. Jika titik tengah berada di luar batas ROI:
       - Bounding box digambar dengan warna abu-abu (150, 150, 150) sebagai penanda tidak aktif.
    5. Menulis teks nama kelas dan persentase confidence di atas bounding box.
    6. Meng-encode frame BGR ke format JPEG menggunakan cv2.imencode.
    
    Return:
        bytes: Data biner citra JPEG terkompresi.
    """
    h, w = img_bgr.shape[:2]

    COLORS = [
        (0, 255, 136),
        (0, 212, 255),
        (255, 149, 0),
        (199, 125, 255),
        (255, 59, 59),
        (255, 209, 102),
    ]

    # Koordinat piksel batas ROI
    zx1, zx2 = int(ROI[0] * w), int(ROI[1] * w)
    zy1, zy2 = int(ROI[2] * h), int(ROI[3] * h)
    cv2.rectangle(img_bgr, (zx1, zy1), (zx2, zy2), (0, 255, 255), 2)
    cv2.putText(img_bgr, "ZONA INSPEKSI", (zx1 + 5, zy1 + 20),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)

    for i, d in enumerate(detections):
        x1n, y1n, x2n, y2n = d["box"]
        cxn = (x1n + x2n) / 2.0
        cyn = (y1n + y2n) / 2.0

        # Verifikasi posisi titik pusat objek terhadap rentang ROI
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

        # Menggambar garis aksen sudut box untuk objek di dalam zona
        if in_zone:
            cs = max(10, min(x2 - x1, y2 - y1) // 5)
            clw = max(3, lw + 1)
            for (cx, cy), dx, dy in [
                ((x1, y1),  1,  1),
                ((x2, y1), -1,  1),
                ((x1, y2),  1, -1),
                ((x2, y2), -1, -1),
            ]:
                cv2.line(img_bgr, (cx, cy), (cx + dx * cs, cy), color, clw)
                cv2.line(img_bgr, (cx, cy), (cx, cy + dy * cs), color, clw)

        # Label teks kelas dan nilai confidence
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
    Logika state-tracking untuk mencatat objek ke database saat keluar dari ROI.
    
    Alur:
    1. Filter deteksi yang titik tengahnya (cx, cy) berada di dalam batas ROI.
    2. Masukkan ke `active_tracking` jika baru, atau perbarui `last_seen` dan `conf` tertinggi jika sudah ada.
    3. Evaluasi semua entri di `active_tracking`:
       Jika (current_timestamp - last_seen) > EXIT_DELAY (1.0 detik):
       - Objek dianggap telah selesai melintasi zona inspeksi.
       - Simpan tuple (waktu, kelas, confidence) ke tabel SQLite `deteksi`.
       - Hapus entri kelas tersebut dari `active_tracking`.
       - Tambahkan data ke list `newly_saved` untuk diteruskan ke event SSE.
       
    Return:
        list: Daftar objek yang berhasil disimpan ke database pada pemanggilan fungsi ini.
    """
    now = time.time()
    newly_saved = []

    with tracking_lock:
        # Perbarui timestamp dan confidence untuk objek di dalam ROI
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

        # Identifikasi objek yang sudah keluar dari ROI melebihi batas EXIT_DELAY
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
# KELAS STREAMING KAMERA (THREADED BUFFERLESS CAPTURE)
# =============================================================================

def pakai_picamera2(src) -> bool:
    """
    Menentukan apakah capture harus menggunakan library Picamera2.
    Return True hanya jika:
      - CAM_BACKEND bukan 'opencv'
      - Library Picamera2 tersedia di sistem
      - Parameter `src` bukan URL string HTTP
      - Picamera2.global_camera_info() mengembalikan sensor kamera yang terpasang.
    """
    if CAM_BACKEND == "opencv" or not PICAMERA2_ADA:
        return False
    if isinstance(src, str) and not src.isdigit():
        return False
    if CAM_BACKEND == "picamera2":
        return True
    try:
        return len(Picamera2.global_camera_info()) > 0
    except Exception:
        return False


class CameraStream:
    """
    Kelas penangkap frame kamera berbasis threading mandiri.
    
    Tujuan teknis:
    OpenCV VideoCapture.read() secara default memblokir thread pemanggil (blocking I/O)
    dan mengantre frame di buffer internal. Jika loop pemrosesan lebih lambat dari FPS kamera,
    frame yang dibaca akan mengalami penundaan (latency build-up).
    
    Implementasi:
    Thread terpisah (`update`) secara kontinyu membaca frame terbaru dari hardware 
    dan menimpa variabel `self.frame`. Thread utama (`camera_loop`) hanya membaca snapshot 
    terakhir tanpa blocking.
    """
    def __init__(self, src):
        self.stopped = False
        self.picam   = None
        self.stream  = None
        self.ret     = False
        self.frame   = None

        # Jalur inisialisasi modul Picamera2 (Raspberry Pi Camera Rev 1.3 via CSI)
        if pakai_picamera2(src):
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
            except Exception as e:
                print(f"Inisialisasi Picamera2 gagal ({e}), beralih ke backend OpenCV.")
                self.picam = None

        # Jalur inisialisasi OpenCV VideoCapture (USB Webcam, IP Camera, atau fallback V4L2)
        if self.picam is None:
            cam_sources = []

            if isinstance(src, int) or (isinstance(src, str) and src.isdigit()):
                idx = int(src)
                candidates = [idx]

                # Di Linux, lakukan pemindaian file device /dev/video* untuk menemukan indeks capture yang valid
                if sys.platform != "win32" and idx == 0:
                    for dev_path in sorted(glob.glob("/dev/video*")):
                        try:
                            num = int(dev_path.replace("/dev/video", ""))
                            if num not in candidates:
                                candidates.append(num)
                        except ValueError:
                            pass

                # Tambahkan opsi backend V4L2 dan CAP_ANY untuk tiap indeks kandidat
                for c in candidates:
                    if sys.platform != "win32":
                        cam_sources.append((c, cv2.CAP_V4L2))
                    cam_sources.append((c, cv2.CAP_ANY))
            else:
                # String URL (RTSP / HTTP stream)
                cam_sources = [(src, cv2.CAP_ANY)]

            # Uji pembukaan stream dan lakukan warmup read hingga 15 kali percobaan
            for s, backend in cam_sources:
                try:
                    cap = cv2.VideoCapture(s, backend) if backend != cv2.CAP_ANY else cv2.VideoCapture(s)
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
                                break
                            time.sleep(0.1)

                        if self.ret:
                            break
                        else:
                            cap.release()
                except Exception:
                    pass

        # Jalankan thread polling frame
        self.thread = threading.Thread(target=self.update, daemon=True)
        self.thread.start()

    def is_opened(self) -> bool:
        """Mengembalikan status kesiapan capture kamera."""
        if self.picam is not None:
            return bool(self.ret)
        return self.stream is not None and self.stream.isOpened()

    def update(self):
        """Loop internal daemon thread untuk membaca frame kamera secara kontinyu."""
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
        """Mengambil pasangan nilai boolean status (ret) dan array citra (frame) terbaru."""
        return self.ret, self.frame

    def stop(self):
        """Menghentikan thread update dan menutup resource hardware kamera."""
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
# PIPELINE UTAMA (THREAD KAMERA & INFERENSI YOLO)
# =============================================================================

def camera_loop():
    """
    Fungsi eksekusi background thread untuk pemrosesan inferensi YOLO dan visualisasi.
    
    Alur per frame:
    1. Mengambil frame BGR dari objek `CameraStream`.
    2. Menghitung increment `frame_count`.
    3. Jika `frame_count % FRAME_SKIP == 0`:
       - Jalankan inferensi `model.predict()` dengan parameter `imgsz=IMGSZ` dan `conf=current_conf`.
       - Ambil bounding box tensor, hitung waktu eksekusi inferensi dalam milidetik (ms).
       - Jalankan `process_tracking()` untuk memperbarui status deteksi komponen pada ROI.
       - Render bounding box dan ROI via `draw_boxes_on_frame()`.
       - Update dictionary `latest_result` dan buffer `latest_frame`.
       - Kirim event update ke client web via `push_sse()`.
    4. Jika bukan frame inferensi:
       - Tetap lakukan rendering frame dengan visualisasi deteksi sebelumnya agar video tidak freeze.
    5. Jeda thread sebesar 10ms (`time.sleep(0.01)`) untuk melepaskan alokasi CPU.
    """
    global latest_frame, latest_result, is_running

    try:
        cap = CameraStream(camera_index)
    except Exception as e:
        print(f"Eksepsi saat membuka CameraStream: {e}")
        is_running = False
        return

    time.sleep(0.5)

    if not cap.is_opened():
        print(f"Kamera index {camera_index} gagal diinisialisasi.")
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

        # Pemrosesan inferensi model pada interval frame_skip
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

            # Ekstraksi nama kelas, score confidence, dan koordinat ternormalisasi (0-1)
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
            # Menggambar overlay deteksi terakhir pada frame yang dilewati inferensinya
            if last_deteksi is not None:
                frame_bytes = draw_boxes_on_frame(frame.copy(), last_deteksi)
                with state_lock:
                    latest_frame = frame_bytes

        # Throttle loop 10 milidetik untuk mencegah thread mendominasi CPU core
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
    """Menyajikan halaman HTML antarmuka dashboard monitoring."""
    return render_template("display.html")


@app.post("/api/start")
def start_cam():
    """
    Endpoint untuk mengaktifkan sesi deteksi.
    Membaca parameter JSON `cam_index` (int atau URL string).
    Memulai thread `camera_loop` dalam mode daemon.
    """
    global is_running, camera_index
    if not is_running:
        try:
            req_data = request.get_json(silent=True) or {}
            if "cam_index" in req_data:
                val = req_data["cam_index"]
                camera_index = val if (isinstance(val, str) and val.startswith("http")) else int(val)
        except Exception:
            pass

        is_running = True
        threading.Thread(target=camera_loop, daemon=True).start()

    return jsonify(status="ok", running=True)


@app.post("/api/stop")
def stop_cam():
    """
    Endpoint untuk menghentikan sesi deteksi.
    Mengubah flag `is_running` menjadi False untuk mengakhiri loop kamera.
    """
    global is_running
    is_running = False
    return jsonify(status="ok", running=False)


@app.post("/api/config")
def set_config():
    """
    Endpoint untuk mengubah parameter confidence threshold secara dinamis.
    Menerima body JSON: {"conf": float}.
    """
    global current_conf
    try:
        current_conf = float(request.json.get("conf", current_conf))
    except (ValueError, TypeError):
        pass
    return jsonify(status="ok", conf=current_conf)


@app.get("/api/history")
def get_history():
    """
    Mengambil data riwayat deteksi dari tabel SQLite `deteksi`.
    Menerima query parameter `limit` (default: 100 record terakhir, order descending).
    """
    limit = int(request.args.get("limit", 100))
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, waktu, kelas, confidence FROM deteksi ORDER BY id DESC LIMIT ?",
            (limit,)
        ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.delete("/api/history")
def clear_history():
    """
    Menghapus seluruh record pada tabel `deteksi` dan mengosongkan dict `active_tracking`.
    Dipanggil saat memulai sesi deteksi baru dari frontend.
    """
    with get_db() as conn:
        conn.execute("DELETE FROM deteksi")
        conn.commit()
    with tracking_lock:
        active_tracking.clear()
    return jsonify(status="ok", message="Riwayat berhasil dihapus")


@app.get("/api/history/export")
def export_history():
    """
    Mengekspor seluruh record tabel `deteksi` ke dalam file CSV di memory (io.StringIO).
    Menggunakan encoding 'utf-8-sig' (dengan UTF-8 Byte Order Mark) agar terbaca rapi pada Microsoft Excel.
    Mengembalikan file download HTTP attachment.
    """
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
    """
    Endpoint Server-Sent Events (SSE) dengan MIME-type text/event-stream.
    Setiap client web yang terhubung akan mendapatkan instance queue.Queue.
    Generator membaca queue dan melakukan `yield` data JSON setiap ada inferensi baru.
    Kirim ping keepalive setiap 15 detik jika antrean kosong.
    """
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
    """
    Endpoint streaming video MJPEG dengan MIME-type multipart/x-mixed-replace; boundary=frame.
    Loop generator mengirimkan byte frame JPEG (`latest_frame`) dibungkus boundary header.
    Jeda 40ms antar frame membatasi throughput streaming maksimal di ~25 FPS.
    """
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
# ENTRY POINT SERVER
# =============================================================================
if __name__ == "__main__":
    # Host 0.0.0.0 agar server dapat diakses dari jaringan lokal (LAN/WiFi).
    # Parameter threaded=True memastikan Flask memproses koneksi SSE, MJPEG, dan REST API
    # pada thread terpisah secara non-blocking.
    app.run(host="0.0.0.0", port=PORT, threaded=True)
