# =============================================================================
# app.py — Backend Server Sistem Deteksi Komponen Elektronika (ElectroCom61)
# =============================================================================
# Arsitektur Multi-Threaded Decoupled:
# 1. Thread Capture (CameraStream):
#    Membaca frame dari sensor kamera (Picamera2 / OpenCV) secara kontinu.
#    Menyimpan frame terbaru tanpa antrean menumpuk (zero-lag).
# 2. Thread Inferensi (inference_loop):
#    Menjalankan model YOLOv8 NCNN secara mandiri. Mengambil frame terbaru saat
#    model siap, menjalankan tracking, dan mengirim telemetri SSE.
#    Frame kamera yang terlewat saat inferensi sibuk otomatis diabaikan.
# 3. Thread Stream Rendering (stream_render_loop) & Endpoint /video/stream:
#    Merender bounding box deteksi terakhir ke SETIAP frame kamera baru,
#    mengompres ke JPEG, dan mengirim ke browser via MJPEG multipart HTTP.
#    Video stream TIDAK PERNAH menunggu inferensi sehingga tampilan di browser
#    tetap mulus (tidak ngeframe / patah-patah).
# 4. Thread Database Writer (db_worker):
#    Menulis data hasil inspeksi ke database SQLite secara asinkron via queue,
#    mencegah disk I/O microSD menghambat pipeline utama.
# =============================================================================

import csv
import io
import json
import os
import queue
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

# Sembunyikan pesan log internal OpenCV yang tidak relevan di terminal
os.environ["OPENCV_LOG_LEVEL"] = "FATAL"
os.environ["OPENCV_VIDEOIO_V4L2_DEBUG"] = "0"

# Alokasi thread CPU untuk Raspberry Pi 4 (Quad-Core):
# 3 core untuk inferensi NCNN, 1 core untuk capture kamera, web stream, dan OS
NCNN_THREADS = int(os.getenv("NCNN_THREADS", "3"))
os.environ["OMP_NUM_THREADS"] = str(NCNN_THREADS)

import cv2
import numpy as np
from flask import Flask, Response, jsonify, render_template, request, send_file

try:
    cv2.setLogLevel(0)
    cv2.setNumThreads(int(os.getenv("OPENCV_THREADS", "2")))
except Exception:
    pass

try:
    import ncnn
    try:
        ncnn.set_omp_num_threads(NCNN_THREADS)
    except Exception:
        pass
except ImportError:
    pass

# Pre-load libopenblas untuk mencegah error symbol sbgemm_ pada PyTorch ARM64 / Raspberry Pi
import ctypes
for _lib in [
    "libopenblas.so.0",
    "libopenblas.so",
    "libblas.so.3",
    "/usr/lib/aarch64-linux-gnu/libopenblas.so.0",
    "/usr/lib/arm-linux-gnueabihf/libopenblas.so.0"
]:
    try:
        ctypes.CDLL(_lib, mode=ctypes.RTLD_GLOBAL)
        break
    except Exception:
        pass

from ultralytics import YOLO

# Modul Picamera2 untuk komunikasi langsung ke sensor kamera CSI (OV5647)
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
DEFAULT_MODEL          = "models/best_ncnn_model"
MODEL_PATH             = os.getenv("MODEL_PATH", DEFAULT_MODEL)
IMGSZ                  = int(os.getenv("IMGSZ", "640"))
CONF                   = float(os.getenv("CONF", "0.60"))
PORT                   = int(os.getenv("PORT", "5000"))
CAM_WIDTH              = int(os.getenv("CAM_WIDTH",  "640"))
CAM_HEIGHT             = int(os.getenv("CAM_HEIGHT", "480"))
CAM_FPS                = int(os.getenv("CAM_FPS", "20"))          # Batasi FPS kamera hardware (15-20)
CAM_BUFFERS            = int(os.getenv("CAM_BUFFERS", "2"))      # Buffer count video Picamera2
CAM_EXPOSURE_US        = int(os.getenv("CAM_EXPOSURE_US", "0"))  # 0=Auto AE; >0 manual exposure (cth: 8000)
CAM_GAIN               = float(os.getenv("CAM_GAIN", "2.5"))     # Analogue gain saat manual exposure
JPEG_QUALITY           = int(os.getenv("JPEG_QUALITY", "65"))    # Kualitas kompresi stream video web
EXIT_DELAY             = 1.0                                     # Toleransi waktu objek keluar ROI (detik)
ROI                    = (0.25, 0.75, 0.1, 0.9)                  # Batas Zona Inspeksi tengah (xmin, xmax, ymin, ymax)
MIN_DETEKSI_PER_OBJEK  = int(os.getenv("MIN_DETEKSI_PER_OBJEK", "3"))
PROFILE_ENABLED        = os.getenv("PROFILE", "0") == "1"

# =============================================================================
# VALIDASI & INISIALISASI MODEL NCNN
# =============================================================================
model_dir = Path(MODEL_PATH)
if not model_dir.exists() or not model_dir.is_dir():
    sys.exit(
        f"\n[FATAL ERROR] Folder model NCNN '{MODEL_PATH}' tidak ditemukan!\n"
        f"Aplikasi ElectroCom61 saat ini menggunakan format NCNN secara eksklusif.\n"
        f"Pastikan folder '{MODEL_PATH}' tersedia dan berisi file model.ncnn.param, "
        f"model.ncnn.bin, serta metadata.yaml.\n"
    )

# Periksa metadata.yaml untuk memverifikasi kesesuaian imgsz ekspor
metadata_path = model_dir / "metadata.yaml"
export_imgsz = None
if metadata_path.exists():
    try:
        import yaml
        with open(metadata_path, "r", encoding="utf-8") as f:
            meta = yaml.safe_load(f)
            if meta and "imgsz" in meta:
                raw_sz = meta["imgsz"]
                export_imgsz = raw_sz[0] if isinstance(raw_sz, (list, tuple)) else int(raw_sz)
    except Exception as e:
        print(f"[PERINGATAN] Gagal membaca metadata.yaml: {e}")

if export_imgsz is not None:
    if IMGSZ != export_imgsz:
        print(
            f"\n[PERINGATAN IMGSZ] Parameter IMGSZ={IMGSZ} berbeda dengan ukuran ekspor "
            f"NCNN ({export_imgsz}) di {metadata_path}!\n"
            f"Model NCNN Ultralytics terikat pada resolusi saat diekspor.\n"
            f"Menyelaraskan IMGSZ = {export_imgsz} secara otomatis agar inferensi valid.\n"
        )
        IMGSZ = export_imgsz
    else:
        print(f"[INFO MODEL] Ukuran input IMGSZ cocok dengan ekspor NCNN: {IMGSZ}")

print(f"[INFO MODEL] Memuat model NCNN dari: {MODEL_PATH}")
model = YOLO(MODEL_PATH)
model_lock = threading.Lock()

# Verifikasi kesesuaian daftar kelas dengan models/classes.txt bila tersedia
classes_file = Path("models/classes.txt")
if classes_file.exists():
    try:
        with open(classes_file, "r", encoding="utf-8") as f:
            expected_classes = [line.strip() for line in f if line.strip()]
        model_classes = list(model.names.values()) if isinstance(model.names, dict) else list(model.names)
        if model_classes != expected_classes:
            print(
                f"[PERINGATAN KELAS] Daftar kelas pada model ({model_classes}) "
                f"berbeda dengan isi file {classes_file} ({expected_classes})!"
            )
        else:
            print(f"[INFO MODEL] Verifikasi kelas: Cocok dengan {classes_file} ({len(model_classes)} kelas).")
    except Exception as e:
        print(f"[PERINGATAN] Gagal memeriksa {classes_file}: {e}")

# Warmup run
print(f"[INFO MODEL] Menjalankan warmup inferensi NCNN (imgsz={IMGSZ})...")
model.predict(np.zeros((IMGSZ, IMGSZ, 3), dtype=np.uint8), imgsz=IMGSZ, verbose=False)
print("[INFO MODEL] Warmup selesai. Model siap digunakan.")

# =============================================================================
# INSTRUMENTASI PROFILING (PROFILE=1)
# =============================================================================

class SystemProfiler:
    """
    Mengukur throughput FPS, latensi per tahap, jeda stream, dan hardware Pi.
    Mencetak ringkasan ke terminal tiap 5 detik jika PROFILE=1.
    """
    def __init__(self, interval: float = 5.0):
        self.interval = interval
        self.lock = threading.Lock()

        self.capture_count = 0
        self.stream_count = 0
        self.inference_count = 0

        self.stage_timings: dict[str, list[float]] = {
            "capture": [],
            "predict": [],
            "draw_boxes": [],
            "cv2.imencode": [],
            "penulisan SQLite": [],
            "push_sse": []
        }

        self.stream_gaps: list[float] = []
        self.detection_ages: list[float] = []
        self.last_stream_send_time: float | None = None

        self.last_report_time = time.time()
        self.running = False
        self.thread: threading.Thread | None = None

    def record_capture(self):
        with self.lock:
            self.capture_count += 1

    def record_stream_send(self):
        now = time.perf_counter()
        with self.lock:
            self.stream_count += 1
            if self.last_stream_send_time is not None:
                gap_ms = (now - self.last_stream_send_time) * 1000.0
                self.stream_gaps.append(gap_ms)
            self.last_stream_send_time = now

    def record_inference(self):
        with self.lock:
            self.inference_count += 1

    def record_stage(self, stage: str, duration_ms: float):
        with self.lock:
            if stage in self.stage_timings:
                self.stage_timings[stage].append(duration_ms)

    def record_detection_age(self, age_ms: float):
        with self.lock:
            self.detection_ages.append(age_ms)

    def start(self):
        if not self.running:
            self.running = True
            self.thread = threading.Thread(target=self._run_loop, daemon=True)
            self.thread.start()

    def _read_pi_diagnostics(self) -> dict[str, str]:
        diag = {"temp": "N/A", "clock": "N/A", "throttled": "N/A"}
        try:
            res = subprocess.run(["vcgencmd", "measure_temp"], capture_output=True, text=True, timeout=1)
            if res.returncode == 0:
                diag["temp"] = res.stdout.strip().replace("temp=", "")
        except Exception:
            pass

        try:
            res = subprocess.run(["vcgencmd", "measure_clock", "arm"], capture_output=True, text=True, timeout=1)
            if res.returncode == 0 and "=" in res.stdout:
                hz = int(res.stdout.strip().split("=")[-1])
                diag["clock"] = f"{hz / 1_000_000:.0f} MHz"
        except Exception:
            pass

        try:
            res = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=1)
            if res.returncode == 0:
                code_str = res.stdout.strip().replace("throttled=", "")
                diag["throttled"] = code_str
                try:
                    code = int(code_str, 16)
                    reasons = []
                    if code & 0x1: reasons.append("Under-voltage")
                    if code & 0x2: reasons.append("Freq-capped")
                    if code & 0x4: reasons.append("Throttled")
                    if code & 0x8: reasons.append("Soft-temp-limit")
                    diag["throttled"] += f" ({', '.join(reasons)})" if reasons else " (Normal / OK)"
                except ValueError:
                    pass
        except Exception:
            pass

        return diag

    def _run_loop(self):
        while self.running:
            time.sleep(self.interval)
            now = time.time()
            with self.lock:
                elapsed = max(0.001, now - self.last_report_time)
                cap_fps = self.capture_count / elapsed
                stream_fps = self.stream_count / elapsed
                inf_fps = self.inference_count / elapsed

                timings_snapshot = {k: list(v) for k, v in self.stage_timings.items()}
                gaps_snapshot = list(self.stream_gaps)
                ages_snapshot = list(self.detection_ages)

                self.capture_count = 0
                self.stream_count = 0
                self.inference_count = 0
                for k in self.stage_timings:
                    self.stage_timings[k].clear()
                self.stream_gaps.clear()
                self.detection_ages.clear()
                self.last_report_time = now

            max_gap = max(gaps_snapshot) if gaps_snapshot else 0.0
            avg_gap = (sum(gaps_snapshot) / len(gaps_snapshot)) if gaps_snapshot else 0.0
            avg_age = (sum(ages_snapshot) / len(ages_snapshot)) if ages_snapshot else 0.0
            max_age = max(ages_snapshot) if ages_snapshot else 0.0

            pi_diag = self._read_pi_diagnostics()

            print("\n" + "=" * 72)
            print(f" [PROFILING REPORT — 5s] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            print("=" * 72)
            print(f" Model Path             : {MODEL_PATH} (imgsz={IMGSZ})")
            print(f" Throughput FPS         :")
            print(f"   - Capture Kamera     : {cap_fps:5.1f} FPS")
            print(f"   - Stream Browser     : {stream_fps:5.1f} FPS (Mulus, non-blocking)")
            print(f"   - Inferensi NCNN     : {inf_fps:5.1f} FPS")
            print(" Latensi per Tahap (ms)  :")
            for stage, vals in timings_snapshot.items():
                if vals:
                    avg_t = sum(vals) / len(vals)
                    max_t = max(vals)
                    count_t = len(vals)
                    print(f"   - {stage:<18}: {avg_t:6.1f} ms  (max: {max_t:6.1f} ms, n={count_t})")
                else:
                    print(f"   - {stage:<18}:    N/A")
            print(" Stream Smoothness & Umur Deteksi:")
            print(f"   - Jeda terpanjang stream: {max_gap:6.1f} ms  (rata-rata: {avg_gap:.1f} ms)")
            print(f"   - Umur hasil deteksi    : {avg_age:6.1f} ms  (max: {max_age:.1f} ms)")
            print(" Diagnostik Raspberry Pi:")
            print(f"   - Suhu CPU           : {pi_diag['temp']}")
            print(f"   - Frekuensi CPU (arm): {pi_diag['clock']}")
            print(f"   - Status Throttled   : {pi_diag['throttled']}")
            print("=" * 72 + "\n")

profiler = SystemProfiler(interval=5.0)
if PROFILE_ENABLED:
    print("[PROFILING] Mode profiling aktif (PROFILE=1). Ringkasan tercetak tiap 5 detik.")
    profiler.start()

# =============================================================================
# MANAJEMEN DATABASE SQLITE DENGAN QUEUE TERPISAH (LANGKAH 3)
# =============================================================================
DB_PATH = Path("data/history.db")
DB_PATH.parent.mkdir(exist_ok=True)

db_write_queue: queue.Queue = queue.Queue()

def get_db():
    conn = sqlite3.connect(str(DB_PATH), timeout=10.0)
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

def db_worker():
    """Worker background thread untuk menulis data ke SQLite secara asinkron."""
    while True:
        task = db_write_queue.get()
        if task is None:
            db_write_queue.task_done()
            break
        try:
            t0 = time.perf_counter()
            with get_db() as conn:
                conn.execute(
                    "INSERT INTO deteksi (waktu, kelas, confidence) VALUES (?,?,?)",
                    (task["waktu"], task["kelas"], task["confidence"])
                )
                conn.commit()
            t1 = time.perf_counter()
            if PROFILE_ENABLED:
                profiler.record_stage("penulisan SQLite", (t1 - t0) * 1000.0)
        except Exception as e:
            print(f"[DB ERROR] Gagal menulis ke database: {e}")
        finally:
            db_write_queue.task_done()

db_thread = threading.Thread(target=db_worker, daemon=True)
db_thread.start()

# =============================================================================
# STATE GLOBAL & SINKRONISASI THREAD
# =============================================================================
state_lock      = threading.Lock()
latest_frame    = None               # Frame JPEG terkompresi untuk video stream
latest_frame_id = -1                # Nomor urut frame JPEG terbaru
latest_result   = {"detections": [], "ms": 0, "ts": 0, "running": False}
current_conf    = CONF
is_running      = False

# Shared raw camera frame & condition
frame_condition     = threading.Condition()
latest_raw_frame    = None
latest_raw_frame_id = 0
latest_raw_frame_ts = 0.0

# Shared inference result
inference_lock      = threading.Lock()
cached_detections: list = []
cached_detection_ts = 0.0

active_tracking: dict[str, dict] = {}
tracking_lock = threading.Lock()

sse_clients: list[queue.Queue] = []
sse_lock = threading.Lock()

app = Flask(__name__)

# =============================================================================
# FUNGSI PEMROSESAN CITRA & LOGIKA TRACKING
# =============================================================================

def push_sse(data: dict):
    """Mendistribusikan data telemetri deteksi ke seluruh browser via SSE."""
    t0 = time.perf_counter()
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
    if PROFILE_ENABLED:
        profiler.record_stage("push_sse", (time.perf_counter() - t0) * 1000.0)


def draw_boxes_on_frame(img_bgr: np.ndarray, detections: list) -> bytes:
    """
    Menggambar visualisasi inspeksi pada frame dan mengompres ke format JPEG:
    1. Kotak kuning pembatas Zona Inspeksi (ROI).
    2. Bounding box berwarna cerah untuk komponen di DALAM zona inspeksi.
    3. Bounding box abu-abu untuk komponen di LUAR zona inspeksi.
    4. Label nama komponen dan persentase nilai confidence.
    """
    t_draw_start = time.perf_counter()
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

    t_draw_end = time.perf_counter()

    t_enc_start = time.perf_counter()
    ok, buf = cv2.imencode(".jpg", img_bgr, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    t_enc_end = time.perf_counter()

    if PROFILE_ENABLED:
        profiler.record_stage("draw_boxes", (t_draw_end - t_draw_start) * 1000.0)
        profiler.record_stage("cv2.imencode", (t_enc_end - t_enc_start) * 1000.0)

    return buf.tobytes() if ok else b""


def process_tracking(detections: list) -> list:
    """
    Logika Debouncing: Menyimpan komponen ke database SQLite tepat 1 kali
    melalui antrean thread terpisah (db_write_queue) agar tidak memblokir inferensi.
    """
    now = time.time()
    newly_saved = []

    with tracking_lock:
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

        for kelas in list(active_tracking.keys()):
            data = active_tracking[kelas]
            if now - data["last_seen"] > EXIT_DELAY:
                waktu_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                conf_val = round(data["conf"], 3)

                # Masukkan ke antrean thread penulis SQLite
                db_write_queue.put({
                    "waktu": waktu_str,
                    "kelas": kelas,
                    "confidence": conf_val
                })

                newly_saved.append({
                    "waktu":      waktu_str,
                    "kelas":      kelas,
                    "confidence": round(data["conf"] * 100, 1)
                })
                del active_tracking[kelas]

    return newly_saved

# =============================================================================
# KELAS KAMERA (THREAD 1: CAPTURE TERPISAH)
# =============================================================================

class CameraStream:
    """
    Thread capture mandiri:
    - Picamera2 (CSI) dengan FrameDurationLimits (hemat beban CPU Pi).
    - Opsi kontrol manual exposure untuk mengurangi motion blur conveyor.
    - buffer_count dikurangi untuk meminimalkan driver latency.
    """
    def __init__(self):
        self.stopped = False
        self.picam   = None
        self.stream  = None
        self.ret     = False
        self.frame   = None
        self.frame_id = 0

        if PICAMERA2_ADA:
            try:
                self.picam = Picamera2(0)
                config = self.picam.create_video_configuration(
                    main={"size": (CAM_WIDTH, CAM_HEIGHT), "format": "RGB888"},
                    buffer_count=CAM_BUFFERS
                )
                self.picam.configure(config)

                controls = {}
                if CAM_FPS > 0:
                    frame_duration = int(1_000_000 / CAM_FPS)
                    controls["FrameDurationLimits"] = (frame_duration, frame_duration)

                if CAM_EXPOSURE_US > 0:
                    controls["AeEnable"] = False
                    controls["ExposureTime"] = CAM_EXPOSURE_US
                    controls["AnalogueGain"] = CAM_GAIN
                    print(f"Kamera: Manual Exposure aktif ({CAM_EXPOSURE_US} us, Gain {CAM_GAIN}x)")
                else:
                    controls["AeEnable"] = True

                self.picam.set_controls(controls)
                self.picam.start()
                time.sleep(0.5)

                self.frame = self.picam.capture_array()
                self.ret   = self.frame is not None
                print(f"Kamera: Terhubung via Picamera2 CSI ({CAM_WIDTH}x{CAM_HEIGHT} @ max {CAM_FPS} FPS)")
            except Exception as e:
                print(f"Picamera2 gagal ({e}), fallback ke OpenCV...")
                self.picam = None

        if self.picam is None:
            backend = cv2.CAP_V4L2 if sys.platform != "win32" else cv2.CAP_ANY
            cap = cv2.VideoCapture(0, backend)
            if not cap.isOpened() and backend != cv2.CAP_ANY:
                cap = cv2.VideoCapture(0)

            if cap.isOpened():
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAM_WIDTH)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAM_HEIGHT)
                if CAM_FPS > 0:
                    cap.set(cv2.CAP_PROP_FPS, CAM_FPS)

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
        global latest_raw_frame, latest_raw_frame_id, latest_raw_frame_ts

        while not self.stopped:
            t0 = time.perf_counter()
            frame = None
            ret = False

            if self.picam is not None:
                try:
                    frame = self.picam.capture_array()
                    ret = frame is not None
                except Exception:
                    time.sleep(0.01)
                    continue
            else:
                if self.stream is None or not self.stream.isOpened():
                    break
                ret, frame = self.stream.read()

            t1 = time.perf_counter()

            if ret and frame is not None:
                now_ts = time.time()
                self.ret = True
                self.frame = frame
                self.frame_id += 1

                with frame_condition:
                    latest_raw_frame = frame
                    latest_raw_frame_id = self.frame_id
                    latest_raw_frame_ts = now_ts
                    frame_condition.notify_all()

                if PROFILE_ENABLED:
                    profiler.record_capture()
                    profiler.record_stage("capture", (t1 - t0) * 1000.0)
            else:
                time.sleep(0.01)

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
# THREAD 2: INFERENSI TERPISAH (INFERENCE LOOP)
# =============================================================================

def inference_loop():
    """
    Menjalankan inferensi model YOLOv8 NCNN secara mandiri:
    - Mengambil frame terbaru saat model siap.
    - Frame kamera lama yang terlewat saat inferensi sibuk otomatis diabaikan.
    - Memproses tracking debouncing dan memicu pengiriman event SSE.
    """
    global latest_result, cached_detections, cached_detection_ts
    last_processed_id = -1

    while is_running:
        with frame_condition:
            while is_running and latest_raw_frame_id == last_processed_id:
                frame_condition.wait(timeout=0.05)
            if not is_running:
                break
            input_frame = latest_raw_frame.copy() if latest_raw_frame is not None else None
            frame_id = latest_raw_frame_id
            frame_ts = latest_raw_frame_ts

        if input_frame is None or frame_id == last_processed_id:
            continue

        last_processed_id = frame_id

        t_infer_start = time.perf_counter()
        with model_lock:
            hasil = model.predict(
                input_frame,
                imgsz=IMGSZ,
                conf=current_conf,
                verbose=False
            )[0]
        t_infer_end = time.perf_counter()

        ms = round((t_infer_end - t_infer_start) * 1000.0)

        if PROFILE_ENABLED:
            profiler.record_inference()
            profiler.record_stage("predict", (t_infer_end - t_infer_start) * 1000.0)

        deteksi = [
            {
                "kelas":      model.names[int(b.cls[0])],
                "confidence": round(float(b.conf[0]), 3),
                "box":        [round(float(v), 4) for v in b.xyxyn[0]],
            }
            for b in hasil.boxes
        ]

        newly_saved = process_tracking(deteksi)

        with inference_lock:
            cached_detections = deteksi
            cached_detection_ts = frame_ts

        result_data = {
            "detections":  deteksi,
            "ms":          ms,
            "ts":          time.time(),
            "running":     True,
            "newly_saved": newly_saved
        }

        with state_lock:
            latest_result = result_data

        push_sse(result_data)

# =============================================================================
# THREAD 3: STREAM RENDERING & ENCODING LOOP
# =============================================================================

def stream_render_loop():
    """
    Mengambil setiap frame kamera baru, menggambar bounding box dari deteksi
    terakhir (tanpa menunggu inferensi selesai), dan mengompres ke format JPEG.
    Hasilnya disimpan ke latest_frame untuk di-stream secara mulus oleh /video/stream.
    """
    global latest_frame, latest_frame_id
    last_rendered_id = -1

    while is_running:
        with frame_condition:
            while is_running and latest_raw_frame_id == last_rendered_id:
                frame_condition.wait(timeout=0.03)
            if not is_running:
                break
            frame_to_draw = latest_raw_frame.copy() if latest_raw_frame is not None else None
            frame_id = latest_raw_frame_id
            frame_ts = latest_raw_frame_ts

        if frame_to_draw is None or frame_id == last_rendered_id:
            continue

        last_rendered_id = frame_id

        with inference_lock:
            current_boxes = list(cached_detections)
            det_ts = cached_detection_ts

        # Hitung dan catat umur hasil deteksi
        if PROFILE_ENABLED and det_ts > 0:
            det_age_ms = (frame_ts - det_ts) * 1000.0
            profiler.record_detection_age(max(0.0, det_age_ms))

        jpeg_bytes = draw_boxes_on_frame(frame_to_draw, current_boxes)

        if jpeg_bytes:
            with state_lock:
                latest_frame = jpeg_bytes
                latest_frame_id = frame_id

# =============================================================================
# CONTROLLER UTAMA KAMERA (START / STOP)
# =============================================================================

def camera_master_loop():
    """Mengelola thread capture, inferensi, dan rendering secara independen."""
    global latest_frame, latest_frame_id, latest_result, is_running

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

    # Jalankan Thread Inferensi dan Thread Rendering
    t_infer = threading.Thread(target=inference_loop, daemon=True)
    t_render = threading.Thread(target=stream_render_loop, daemon=True)

    t_infer.start()
    t_render.start()

    # Loop pengawas
    while is_running:
        time.sleep(0.2)

    # Bersihkan saat dihentikan
    cap.stop()
    with frame_condition:
        frame_condition.notify_all()

    t_infer.join(timeout=1.0)
    t_render.join(timeout=1.0)

    # Pastikan seluruh antrean penulisan database tersimpan ke disk
    db_write_queue.join()

    with state_lock:
        latest_frame  = None
        latest_frame_id = -1
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
    global is_running
    if not is_running:
        is_running = True
        threading.Thread(target=camera_master_loop, daemon=True).start()
    return jsonify(status="ok", running=True)

@app.post("/api/stop")
def stop_cam():
    global is_running
    is_running = False
    return jsonify(status="ok", running=False)

@app.post("/api/config")
def set_config():
    global current_conf
    try:
        current_conf = float(request.json.get("conf", current_conf))
    except (ValueError, TypeError):
        pass
    return jsonify(status="ok", conf=current_conf)

@app.get("/api/history")
def get_history():
    limit = int(request.args.get("limit", 100))
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, waktu, kelas, confidence FROM deteksi ORDER BY id DESC LIMIT ?",
            (limit,)
        ).fetchall()
    return jsonify([dict(r) for r in rows])

@app.delete("/api/history")
def clear_history():
    with get_db() as conn:
        conn.execute("DELETE FROM deteksi")
        conn.commit()
    with tracking_lock:
        active_tracking.clear()
    return jsonify(status="ok", message="Riwayat berhasil dihapus")

@app.get("/api/history/export")
def export_history():
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
    Generator video stream MJPEG:
    - Mengirim frame hanya jika ada frame baru yang sudah di-encode.
    - Tidak pernah memblokir saat inferensi berjalan (stream mulus).
    """
    def generate():
        last_sent_id = -1
        while True:
            frame_to_send = None

            with state_lock:
                running = is_running
                current_id = latest_frame_id
                current_bytes = latest_frame

            if not running:
                time.sleep(0.5)
                continue

            if current_id != last_sent_id and current_bytes is not None:
                last_sent_id = current_id
                frame_to_send = current_bytes

            if frame_to_send is not None:
                if PROFILE_ENABLED:
                    profiler.record_stream_send()
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n" + frame_to_send + b"\r\n"
                )
            else:
                time.sleep(0.01)

    return Response(generate(), mimetype="multipart/x-mixed-replace; boundary=frame")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, threaded=True)
