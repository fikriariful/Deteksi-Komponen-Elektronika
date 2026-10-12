# ==============================================================================
# SISTEM DETEKSI KOMPONEN ELEKTRONIKA BERBASIS YOLOv8 & FLASK (ElectroCom61)
# ==============================================================================
# File ini merupakan backend utama aplikasi. Bertugas untuk:
# 1. Menghubungkan kamera (Webcam USB / Raspberry Pi Camera via Picamera2 / IP Cam).
# 2. Menjalankan inferensi model AI (YOLOv8 format ONNX) secara real-time.
# 3. Menerapkan algoritma Tracking & Zona Inspeksi (ROI) dengan mekanisme debounce.
# 4. Menyimpan riwayat deteksi ke database lokal SQLite.
# 5. Mengirimkan video stream (MJPEG) dan data telemetri (SSE - Server-Sent Events) ke Web.
# ==============================================================================

import json
import os
import queue
import sqlite3
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

# ------------------------------------------------------------------------------
# PENGATURAN LOGGING OPENCV
# ------------------------------------------------------------------------------
# Mengurangi pesan peringatan/log debug internal dari OpenCV di Linux/Raspberry Pi
# (terutama error ioctl V4L2) agar terminal tetap bersih dan mudah dibaca.
os.environ["OPENCV_LOG_LEVEL"] = "FATAL"
os.environ["OPENCV_VIDEOIO_V4L2_DEBUG"] = "0"

import cv2
import numpy as np
from flask import Flask, Response, jsonify, render_template, request, send_file

# Matikan log level OpenCV jika didukung
try:
    cv2.setLogLevel(0)
except Exception:
    pass

from ultralytics import YOLO

# ------------------------------------------------------------------------------
# INISIALISASI PICAMERA2 (KHUSUS RASPBERRY PI)
# ------------------------------------------------------------------------------
# Picamera2 adalah library resmi Raspberry Pi OS untuk mengakses modul Pi Camera.
# Jika dijalankan di virtual environment (venv), modul ini seringkali ada di sistem
# utama (/usr/lib/python3/dist-packages), sehingga kita tambahkan ke path pencarian.
try:
    from picamera2 import Picamera2
    PICAMERA2_ADA = True
except ImportError:
    sys.path.append("/usr/lib/python3/dist-packages")
    try:
        from picamera2 import Picamera2
        PICAMERA2_ADA = True
    except ImportError:
        # Jika bukan di Raspberry Pi (misal di Laptop/PC Windows), gunakan OpenCV
        PICAMERA2_ADA = False

# ==============================================================================
# KONFIGURASI SISTEM & PARAMETER INFERENSI
# ==============================================================================
# Nilai-nilai ini dapat diatur melalui environment variable atau menggunakan nilai default.
MODEL_PATH   = os.getenv("MODEL_PATH", "models/best.onnx")  # Path ke model YOLOv8 (ONNX)
IMGSZ        = int(os.getenv("IMGSZ", "320"))               # Ukuran resolusi input model (320x320 optimal untuk performa Pi)
CONF         = float(os.getenv("CONF", "0.50"))             # Ambang batas kepercayaan (confidence threshold 50%)
PORT         = int(os.getenv("PORT", "5000"))               # Port web server Flask

# FRAME_SKIP: Teknik optimasi CPU. Inferensi model hanya dijalankan setiap N frame.
# Contoh: Jika bernilai 3, inferensi berjalan di frame ke-3, ke-6, ke-9, dst.
# Frame di antaranya tetap ditampilkan dengan bounding box sebelumnya (hemat CPU hingga 60%).
FRAME_SKIP   = int(os.getenv("FRAME_SKIP", "3"))

# CAM_WIDTH & CAM_HEIGHT: Resolusi tangkapan kamera asli dari sensor (640x480).
CAM_WIDTH    = int(os.getenv("CAM_WIDTH",  "1280"))  # Resolusi HD 720p (16:9 widescreen)
CAM_HEIGHT   = int(os.getenv("CAM_HEIGHT", "720"))   # Pas penuh di layar monitor

# JPEG_QUALITY: Kompresi JPEG untuk streaming video ke web (skala 0-100).
# Nilai 65 memberikan keseimbangan bagus antara ketajaman gambar dan bandwidth jaringan.
JPEG_QUALITY = int(os.getenv("JPEG_QUALITY", "75"))   # Kualitas kompresi ditingkatkan agar lebih jernih

# CAM_BACKEND: Menentukan driver kamera yang digunakan:
#   'auto'      -> Mendeteksi otomatis Pi Camera, jika tidak ada beralih ke OpenCV
#   'opencv'    -> Memaksa menggunakan OpenCV (misal untuk USB Webcam)
#   'picamera2' -> Memaksa menggunakan Picamera2
CAM_BACKEND  = os.getenv("CAM_BACKEND", "auto")

# EXIT_DELAY: Waktu toleransi (dalam detik) setelah objek tidak lagi terlihat di zona inspeksi
# sebelum dinyatakan "benar-benar selesai diperiksa" dan dicatat ke database.
EXIT_DELAY   = 1.0

# ROI (Region of Interest) / ZONA INSPEKSI:
# Format: (x_min, x_max, y_min, y_max) dalam rentang rasio normalisasi 0.0 sampai 1.0.
# Objek hanya akan dihitung/disimpan jika berada di dalam area kotak tengah ini.
# (0.25 ke 0.75 horizontal = 50% area tengah layar; 0.1 ke 0.9 vertikal).
ROI          = (0.25, 0.75, 0.1, 0.9)

# ==============================================================================
# INISIALISASI MODEL YOLOv8 & WARM-UP
# ==============================================================================
# Memuat model ONNX ke dalam memory
model      = YOLO(MODEL_PATH)
model_lock = threading.Lock()  # Mutex Lock untuk memastikan thread-safety saat inferensi

# WARM-UP: Menjalankan satu kali prediksi gambar kosong dummy (array nol).
# Tujuannya menginisialisasi engine ONNX Runtime ke RAM/CPU di awal,
# sehingga saat kamera mulai berjalan, tidak terjadi freeze/lag pada frame pertama.
model.predict(np.zeros((IMGSZ, IMGSZ, 3), dtype=np.uint8), imgsz=IMGSZ, verbose=False)

# ==============================================================================
# PENGELOLAAN DATABASE (SQLite)
# ==============================================================================
DB_PATH = Path("data/history.db")
DB_PATH.parent.mkdir(exist_ok=True)  # Pastikan folder 'data/' sudah dibuat

def get_db():
    """
    Membuka koneksi ke database SQLite lokal.
    Menggunakan row_factory = sqlite3.Row agar data kolom dapat diakses seperti dictionary.
    """
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """
    Membuat tabel 'deteksi' jika belum ada.
    Menyimpan data riwayat: id (auto increment), waktu (string ISO), kelas (nama komponen),
    dan confidence (skor akurasi deteksi).
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

init_db()  # Inisialisasi tabel saat program pertama kali dijalankan

# ==============================================================================
# GLOBAL STATE & THREAD SYNCHRONIZATION
# ==============================================================================
# state_lock: Mengunci variabel state bersama agar tidak terjadi race-condition
# antara thread pemrosesan kamera dan thread request HTTP Flask.
state_lock    = threading.Lock()
latest_frame  = None  # Menyimpan frame JPEG terkompresi terakhir untuk streaming
latest_result = {"detections": [], "ms": 0, "ts": 0, "running": False}  # Data telemetri terakhir
current_conf  = CONF   # Nilai confidence threshold aktif (dapat diubah dinamis dari web)
is_running    = False  # Status apakah kamera dan loop inferensi sedang aktif
camera_index  = int(os.getenv("CAM_INDEX", "0"))  # Indeks kamera terpilih (0, 1, atau URL)

# TRACKING STATE:
# Menyimpan status objek yang saat ini berada di dalam ROI.
# Struktur: {nama_kelas: {"last_seen": timestamp, "conf": nilai_confidence_tertinggi}}
active_tracking: dict[str, dict] = {}
tracking_lock = threading.Lock()

# SSE (Server-Sent Events) CLIENTS:
# Antrean pesan (Queue) untuk setiap browser client yang terhubung ke live dashboard.
sse_clients: list[queue.Queue] = []
sse_lock = threading.Lock()

# Inisialisasi aplikasi Flask
app = Flask(__name__)

# ==============================================================================
# FUNGSI BANTU (HELPERS)
# ==============================================================================
def push_sse(data: dict):
    """
    Mengirimkan data JSON ke semua browser yang sedang membuka halaman web
    melalui protokol Server-Sent Events (SSE).
    Jika ada antrean yang penuh atau client putus, antrean tersebut dibersihkan.
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
    Menggambar visualisasi bounding box, label nama komponen, akurasi %,
    dan batas ZONA INSPEKSI (ROI) langsung pada frame gambar OpenCV.
    
    Output: Buffer bytes dalam format JPEG yang siap dikirimkan ke web browser.
    """
    h, w = img_bgr.shape[:2]
    # Palet warna futuristik untuk membedakan deteksi tiap objek
    COLORS = [(0, 255, 136), (0, 212, 255), (255, 149, 0),
              (199, 125, 255), (255, 59, 59), (255, 209, 102)]
              
    # 1. Gambar Kotak ZONA INSPEKSI (ROI) berwarna kuning di area tengah layar
    zx1, zx2 = int(ROI[0] * w), int(ROI[1] * w)
    zy1, zy2 = int(ROI[2] * h), int(ROI[3] * h)
    cv2.rectangle(img_bgr, (zx1, zy1), (zx2, zy2), (0, 255, 255), 2)
    cv2.putText(img_bgr, "ZONA INSPEKSI", (zx1 + 5, zy1 + 20), 
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)

    # 2. Gambar Bounding Box untuk setiap objek yang terdeteksi
    for i, d in enumerate(detections):
        x1n, y1n, x2n, y2n = d["box"]
        # Titik tengah (Centroid) objek dalam koordinat normalisasi (0.0 - 1.0)
        cxn, cyn = (x1n + x2n) / 2, (y1n + y2n) / 2
        
        # Evaluasi: Apakah titik tengah objek berada di dalam Zona Inspeksi?
        in_zone = (ROI[0] <= cxn <= ROI[1]) and (ROI[2] <= cyn <= ROI[3])
        
        # Konversi koordinat normalisasi ke koordinat piksel frame
        x1, y1 = int(x1n * w), int(y1n * h)
        x2, y2 = int(x2n * w), int(y2n * h)
        
        if in_zone:
            color = COLORS[i % len(COLORS)]  # Beri warna terang jika objek masuk zona aktif
            lw = max(2, w // 350)            # Ketebalan garis proporsional terhadap resolusi
            text_color = (0, 0, 0)
        else:
            color = (150, 150, 150)          # Warna abu-abu redup jika di luar zona (diabaikan)
            lw = 1
            text_color = (255, 255, 255)

        # Gambar kotak pembatas utama (Bounding Box)
        cv2.rectangle(img_bgr, (x1, y1), (x2, y2), color, lw)
        
        # Aksen sudut (Corner Accents) agar tampilan HUD lebih profesional
        if in_zone:
            cs = max(10, min(x2 - x1, y2 - y1) // 5)
            clw = max(3, lw + 1)
            for (cx, cy), dx, dy in [
                ((x1, y1),  1,  1), ((x2, y1), -1,  1),
                ((x1, y2),  1, -1), ((x2, y2), -1, -1),
            ]:
                cv2.line(img_bgr, (cx, cy), (cx + dx * cs, cy), color, clw)
                cv2.line(img_bgr, (cx, cy), (cx, cy + dy * cs), color, clw)
                
        # Label teks: "Nama Komponen Persentase%"
        label = f"{d['kelas']}  {int(d['confidence'] * 100)}%"
        fscale = max(0.4, w / 1200)
        fthick = max(1, w // 600)
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, fscale, fthick)
        ly = max(y1, th + 6)
        
        # Latar belakang label agar teks selalu terbaca jelas di kondisi apapun
        cv2.rectangle(img_bgr, (x1, ly - th - 6), (x1 + tw + 8, ly), color, -1)
        cv2.putText(img_bgr, label, (x1 + 4, ly - 4),
                    cv2.FONT_HERSHEY_SIMPLEX, fscale, text_color, fthick, cv2.LINE_AA)
                    
    # Kompresi gambar BGR ke format JPEG untuk dikirim via jaringan
    ok, buf = cv2.imencode(".jpg", img_bgr, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    return buf.tobytes() if ok else b""

def process_tracking(detections: list) -> list:
    """
    LOGIKA TRACKING & DEBOUNCE PENYIMPANAN DATA:
    Mencegah pencatatan ganda saat objek berada di konveyor/zona inspeksi.
    
    Prinsip Kerja:
    1. Saat objek berada di dalam ROI, perbarui status 'last_seen' dan simpan skor confidence
       tertinggi yang sempat tercapai selama objek melintas.
    2. Saat objek menghilang dari ROI selama lebih dari EXIT_DELAY (1.0 detik), objek
       dinyatakan telah selesai melintas. Data disimpan 1 KALI ke database SQLite.
    3. Kembalikan daftar item baru yang disimpan agar dapat langsung ditampilkan di UI.
    """
    now = time.time()
    newly_saved = []

    with tracking_lock:
        # Langkah 1: Pantau dan catat semua objek yang saat ini ada di DALAM ZONA INSPEKSI
        for d in detections:
            cx = (d["box"][0] + d["box"][2]) / 2
            cy = (d["box"][1] + d["box"][3]) / 2
            
            # Cek apakah koordinat titik tengah objek masuk ke dalam batas ROI
            if ROI[0] <= cx <= ROI[1] and ROI[2] <= cy <= ROI[3]:
                kelas = d["kelas"]
                if kelas not in active_tracking:
                    # Objek baru pertama kali masuk zona
                    active_tracking[kelas] = {"last_seen": now, "conf": d["confidence"]}
                else:
                    # Objek masih berada di zona, perbarui waktu terakhir terlihat
                    active_tracking[kelas]["last_seen"] = now
                    # Simpan akurasi paling tinggi yang didapat selama objek berjalan
                    if d["confidence"] > active_tracking[kelas]["conf"]:
                        active_tracking[kelas]["conf"] = d["confidence"]
        
        # Langkah 2: Evaluasi objek yang SUDAH KELUAR ZONA (Hilang lebih lama dari EXIT_DELAY)
        for kelas in list(active_tracking.keys()):
            data = active_tracking[kelas]
            if now - data["last_seen"] > EXIT_DELAY:
                waktu_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                # Simpan hasil akhir ke database SQLite
                with get_db() as conn:
                    conn.execute(
                        "INSERT INTO deteksi (waktu, kelas, confidence) VALUES (?,?,?)",
                        (waktu_str, kelas, round(data["conf"], 3))
                    )
                    conn.commit()
                
                # Masukkan ke daftar rekaman baru untuk notifikasi UI
                newly_saved.append({
                    "waktu": waktu_str,
                    "kelas": kelas,
                    "confidence": round(data["conf"] * 100, 1)
                })
                # Hapus dari tracking aktif agar siap menerima komponen berikutnya
                del active_tracking[kelas]

    return newly_saved

# ==============================================================================
# ZERO-LATENCY CAMERA STREAM (THREADED CAMERA CAPTURE)
# ==============================================================================
import glob

def pakai_picamera2(src) -> bool:
    """
    Mengecek apakah sumber input harus menggunakan library Picamera2.
    Return True jika sistem berjalan di Raspberry Pi dan terdeteksi modul Pi Camera.
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
    Kelas penangkap frame kamera berbasis Thread terpisah (Zero-Latency Pattern).
    Masalah umum OpenCV adalah penumpukan buffer antrean (frame buffer delay).
    Dengan membaca kamera secara kontinu di background thread dan hanya menyimpan
    frame terbaru (self.frame), latensi streaming menjadi 0 ms (real-time tanpa delay).
    """
    def __init__(self, src):
        self.stopped = False
        self.picam   = None
        self.stream  = None
        self.ret     = False
        self.frame   = None

        # Skenario A: Menggunakan modul Raspberry Pi Camera (Picamera2)
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
                print(f"Kamera: Pi Camera (Picamera2) {CAM_WIDTH}x{CAM_HEIGHT}")
            except Exception as e:
                print(f"Gagal inisialisasi Picamera2: {e}, mencoba OpenCV...")
                self.picam = None

        # Skenario B: Fallback ke OpenCV (Webcam USB / IP Camera / DroidCam / Laptop)
        if self.picam is None:
            cam_sources = []
            if isinstance(src, int) or (isinstance(src, str) and src.isdigit()):
                idx = int(src)
                candidates = [idx]
                # Hanya di Linux / Raspberry Pi dan khusus Kamera 0, bantu cari node fisik /dev/video*
                if sys.platform != "win32" and idx == 0:
                    for dev_path in sorted(glob.glob("/dev/video*")):
                        try:
                            num = int(dev_path.replace("/dev/video", ""))
                            if num not in candidates:
                                candidates.append(num)
                        except ValueError:
                            pass

                for c in candidates:
                    if sys.platform != "win32":
                        cam_sources.append((c, cv2.CAP_V4L2))
                    cam_sources.append((c, cv2.CAP_ANY))
            else:
                # Sumber berupa string URL (misal RTSP atau HTTP MJPEG DroidCam)
                cam_sources = [(src, cv2.CAP_ANY)]

            # Coba buka sumber kamera yang tersedia satu per satu
            for s, backend in cam_sources:
                try:
                    cap = cv2.VideoCapture(s, backend) if backend != cv2.CAP_ANY else cv2.VideoCapture(s)
                    if cap.isOpened():
                        # Set buffer size ke 1 agar tidak terjadi lag penumpukan frame lama
                        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                        cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAM_WIDTH)
                        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAM_HEIGHT)
                        
                        # Warm-up kamera (baca hingga 15 frame) agar sensor kamera siap & stabil
                        for _ in range(15):
                            ret, frame = cap.read()
                            if ret and frame is not None and frame.size > 0:
                                self.stream = cap
                                self.ret = True
                                self.frame = frame
                                print(f"Kamera: OpenCV berhasil membaca {s} (backend={backend})")
                                break
                            time.sleep(0.1)
                            
                        if self.ret:
                            break
                        else:
                            cap.release()
                except Exception as ex:
                    pass

            if not self.ret:
                print(f"Peringatan: Tidak dapat membaca frame dari sumber kamera {src}")

        # Jalankan background thread untuk membaca frame kamera secara terus-menerus
        self.thread = threading.Thread(target=self.update, daemon=True)
        self.thread.start()

    def is_opened(self) -> bool:
        """Memeriksa apakah perangkat kamera berhasil dibuka dan aktif."""
        if self.picam is not None:
            return bool(self.ret)
        return self.stream is not None and self.stream.isOpened()

    def update(self):
        """Loop di thread terpisah: membaca frame kamera terbaru secepat mungkin."""
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
        """Mengambil frame gambar terbaru yang telah ditangkap oleh thread."""
        return self.ret, self.frame

    def stop(self):
        """Menghentikan thread dan melepaskan resource kamera."""
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

# ==============================================================================
# PIPELINE UTAMA: THREAD KAMERA & INFERENSI YOLOv8
# ==============================================================================
def camera_loop():
    """
    Fungsi loop pekerja (background worker thread):
    1. Membaca frame dari CameraStream.
    2. Menjalankan inferensi YOLOv8 dengan mekanisme Frame Skipping.
    3. Menjalankan tracking zona ROI dan pencatatan ke database SQLite.
    4. Menggambar anotasi bounding box pada gambar.
    5. Mendistribusikan data ke client via SSE dan MJPEG.
    """
    global latest_frame, latest_result, is_running

    try:
        cap = CameraStream(camera_index)
    except Exception as e:
        print(f"Gagal membuka kamera: {camera_index} ({e})")
        is_running = False
        return
    time.sleep(0.5)

    if not cap.is_opened():
        print(f"Gagal membuka kamera: {camera_index}")
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

        # ── OPTIMASI FRAME SKIP ──────────────────────────────────────────────
        # Hanya jalankan inferensi YOLO jika frame_count kelipatan dari FRAME_SKIP
        if frame_count % FRAME_SKIP == 0:
            mulai = time.perf_counter()
            with model_lock:
                # Inferensi model YOLOv8 (format ONNX)
                hasil = model.predict(frame.copy(), imgsz=IMGSZ,
                                      conf=current_conf, verbose=False)[0]
            # Hitung waktu inferensi murni dalam satuan milidetik (ms)
            ms = round((time.perf_counter() - mulai) * 1000)

            # Ekstrak data hasil prediksi (nama kelas, confidence score, bounding box xyxyn)
            last_deteksi = [
                {
                    "kelas":      model.names[int(b.cls[0])],
                    "confidence": round(float(b.conf[0]), 3),
                    "box":        [round(float(v), 4) for v in b.xyxyn[0]],
                }
                for b in hasil.boxes
            ]

            # Proses tracking zona dan simpan ke database jika objek keluar zona
            newly_saved = process_tracking(last_deteksi)
            # Gambar visualisasi HUD ke frame
            last_frame_bytes = draw_boxes_on_frame(frame.copy(), last_deteksi)

            # Siapkan paket data telemetri untuk dikirim ke Web UI
            result_data = {
                "detections":  last_deteksi,
                "ms":          ms,
                "ts":          time.time(),
                "running":     True,
                "newly_saved": newly_saved
            }

            # Simpan ke state global yang aman dari thread collision
            with state_lock:
                latest_frame  = last_frame_bytes
                latest_result = result_data

            # Broadcast telemetri ke semua browser client via Server-Sent Events
            push_sse(result_data)
        else:
            # FRAME DILEWATI: Tetap perbarui gambar dengan deteksi sebelumnya agar video mulus
            if last_deteksi is not None:
                frame_bytes = draw_boxes_on_frame(frame.copy(), last_deteksi)
                with state_lock:
                    latest_frame = frame_bytes

        # Jeda 10 milidetik agar CPU tidak 100% dan mencegah overheating pada Raspberry Pi
        time.sleep(0.01)

    # Saat is_running bernilai False, lepaskan kamera dan reset status
    cap.stop()
    with state_lock:
        latest_frame  = None
        latest_result = {"detections": [], "ms": 0, "ts": 0,
                         "running": False, "newly_saved": []}
    push_sse(latest_result)

# ==============================================================================
# ROUTE WEB & API CONTROLLER (FLASK ENDPOINTS)
# ==============================================================================

# 1. Halaman Tampilan Utama
@app.get("/")
def index():
    """Merender template antarmuka pengguna (display.html)."""
    return render_template("display.html")

# 2. Endpoint Kontrol Kamera & Sistem
@app.post("/api/start")
def start_cam():
    """
    Memulai sistem deteksi dan kamera.
    Menerima parameter JSON 'cam_index' (bisa berupa angka 0, 1 atau link IP Cam DroidCam).
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
        # Jalankan loop kamera dalam thread terpisah agar server web tidak freeze
        threading.Thread(target=camera_loop, daemon=True).start()
    return jsonify(status="ok", running=True)

@app.post("/api/stop")
def stop_cam():
    """Menghentikan proses pengambilan frame dan deteksi kamera."""
    global is_running
    is_running = False
    return jsonify(status="ok", running=False)

@app.post("/api/config")
def set_config():
    """
    Mengubah parameter confidence threshold secara langsung (real-time)
    tanpa perlu me-restart server.
    """
    global current_conf
    try:
        current_conf = float(request.json.get("conf", current_conf))
    except (ValueError, TypeError):
        pass
    return jsonify(status="ok", conf=current_conf)

# 3. Endpoint Riwayat & Data Inspeksi
@app.get("/api/history")
def get_history():
    """
    Mengambil data riwayat hasil inspeksi yang tersimpan di SQLite.
    Default mengembalikan 100 rekaman terakhir diurutkan dari yang terbaru.
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
    Menghapus seluruh rekaman riwayat di tabel SQLite dan mereset antrean tracking.
    Dipanggil saat pengguna memulai sesi pengujian baru.
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
    Mengekspor seluruh riwayat inspeksi dari SQLite ke format file CSV/Excel.
    Menggunakan encoding utf-8-sig (Byte Order Mark) agar karakter dan format
    tabel otomatis rapi saat dibuka langsung di Microsoft Excel.
    """
    import csv, io
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
    buf = io.BytesIO(output.getvalue().encode("utf-8-sig"))
    filename = f"riwayat_deteksi_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return send_file(buf, mimetype="text/csv",
                     as_attachment=True, download_name=filename)

# 4. Endpoint Streaming Telemetri (SSE) & Video Feed (MJPEG)
@app.get("/results/stream")
def results_stream():
    """
    Endpoint Server-Sent Events (SSE).
    Mengirimkan stream data JSON secara kontinu ke web browser setiap kali ada deteksi baru,
    mencakup latensi inferensi (ms), daftar objek, dan rekaman baru.
    """
    def event_stream(q: queue.Queue):
        with state_lock:
            init = latest_result
        # Kirim status inisial terlebih dahulu
        yield "data: " + json.dumps(init, ensure_ascii=False) + "\n\n"
        while True:
            try:
                # Tunggu data baru dari queue maksimal 15 detik
                yield q.get(timeout=15)
            except queue.Empty:
                # Kirim keepalive komentar agar koneksi HTTP tidak terputus oleh browser
                yield ": keepalive\n\n"

    q: queue.Queue = queue.Queue(maxsize=10)
    with sse_lock:
        sse_clients.append(q)

    resp = Response(event_stream(q), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @resp.call_on_close
    def remove_client():
        """Membersihkan antrean client jika tab browser ditutup."""
        with sse_lock:
            if q in sse_clients:
                sse_clients.remove(q)
    return resp

@app.get("/video/stream")
def video_stream():
    """
    Endpoint Video Streaming menggunakan standar MJPEG (Motion JPEG).
    Mengirimkan potongan frame gambar JPEG secara berkelanjutan dengan boundary 'frame'.
    Browser dapat menampilkan feed ini langsung menggunakan tag standar <img src="/video/stream">.
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
                yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n")
            time.sleep(0.05)  # Interval pengiriman frame (~20 FPS)
    return Response(generate(), mimetype="multipart/x-mixed-replace; boundary=frame")

# ==============================================================================
# TITIK MASUK PROGRAM UTAMA (ENTRY POINT)
# ==============================================================================
if __name__ == "__main__":
    # Menjalankan server Flask pada seluruh interface jaringan (0.0.0.0) di port 5000.
    # Mode threaded diaktifkan agar mampu melayani streaming video dan request API secara bersamaan.
    app.run(host="0.0.0.0", port=PORT, threaded=True)