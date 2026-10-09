import json
import os
import queue
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from flask import Flask, Response, jsonify, render_template, request, send_file

from ultralytics import YOLO

# Picamera2 hanya ada di Raspberry Pi. Di laptop import ini gagal, dan itu tidak masalah
# karena kamera otomatis dibaca lewat OpenCV seperti biasa.
try:
    from picamera2 import Picamera2
    PICAMERA2_ADA = True
except ImportError:
    PICAMERA2_ADA = False

# ── Konfigurasi ───────────────────────────────────────────────────────────────
MODEL_PATH   = os.getenv("MODEL_PATH", "models/best.onnx")
IMGSZ        = int(os.getenv("IMGSZ", "320"))      # 320 lebih ringan di Raspberry Pi
CONF         = float(os.getenv("CONF", "0.50"))
PORT         = int(os.getenv("PORT", "5000"))
# Inferensi hanya setiap N frame — hemat CPU Raspberry Pi (set 1 di laptop)
FRAME_SKIP   = int(os.getenv("FRAME_SKIP", "3"))
# Resolusi kamera input (lebih kecil = lebih ringan)
CAM_WIDTH    = int(os.getenv("CAM_WIDTH",  "640"))
CAM_HEIGHT   = int(os.getenv("CAM_HEIGHT", "480"))
# Kualitas JPEG streaming
JPEG_QUALITY = int(os.getenv("JPEG_QUALITY", "65"))
# Backend kamera untuk "Kamera 0":
#   auto      -> pakai Picamera2 kalau ada Pi Camera terdeteksi, kalau tidak pakai OpenCV
#   opencv    -> paksa OpenCV (misalnya untuk webcam USB di Raspberry Pi)
#   picamera2 -> paksa Picamera2
CAM_BACKEND  = os.getenv("CAM_BACKEND", "auto")

# Waktu (detik) sebelum barang yang keluar dari "Zona" dianggap benar-benar lewat dan disimpan
EXIT_DELAY = 1.0
# ROI (Region of Interest) / Zona Inspeksi (x_min, x_max, y_min, y_max dalam rasio 0-1)
ROI = (0.25, 0.75, 0.1, 0.9)

# ── Model ─────────────────────────────────────────────────────────────────────
model      = YOLO(MODEL_PATH)
model_lock = threading.Lock()
model.predict(np.zeros((IMGSZ, IMGSZ, 3), dtype=np.uint8), imgsz=IMGSZ, verbose=False)

# ── Database SQLite ───────────────────────────────────────────────────────────
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
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                waktu     TEXT    NOT NULL,
                kelas     TEXT    NOT NULL,
                confidence REAL   NOT NULL
            )
        """)
        conn.commit()

init_db()

# ── State bersama ─────────────────────────────────────────────────────────────
state_lock    = threading.Lock()
latest_frame  = None
latest_result = {"detections": [], "ms": 0, "ts": 0, "running": False}
current_conf  = CONF
is_running    = False
camera_index  = int(os.getenv("CAM_INDEX", "0"))

# Tracking barang di dalam zona: {kelas: {"last_seen": ts, "conf": max_conf}}
active_tracking: dict[str, dict] = {}
tracking_lock = threading.Lock()

sse_clients: list[queue.Queue] = []
sse_lock = threading.Lock()

app = Flask(__name__)

# ── Helpers ───────────────────────────────────────────────────────────────────
def push_sse(data: dict):
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
    h, w = img_bgr.shape[:2]
    COLORS = [(0, 255, 136), (0, 212, 255), (255, 149, 0),
              (199, 125, 255), (255, 59, 59), (255, 209, 102)]
              
    # Gambar Kotak ZONA INSPEKSI di tengah
    zx1, zx2 = int(ROI[0] * w), int(ROI[1] * w)
    zy1, zy2 = int(ROI[2] * h), int(ROI[3] * h)
    cv2.rectangle(img_bgr, (zx1, zy1), (zx2, zy2), (0, 255, 255), 2)
    cv2.putText(img_bgr, "ZONA INSPEKSI", (zx1 + 5, zy1 + 20), 
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)

    for i, d in enumerate(detections):
        x1n, y1n, x2n, y2n = d["box"]
        cxn, cyn = (x1n + x2n) / 2, (y1n + y2n) / 2
        
        # Cek apakah objek ada di dalam Zona
        in_zone = (ROI[0] <= cxn <= ROI[1]) and (ROI[2] <= cyn <= ROI[3])
        
        x1, y1 = int(x1n * w), int(y1n * h)
        x2, y2 = int(x2n * w), int(y2n * h)
        
        if in_zone:
            color = COLORS[i % len(COLORS)] # Warna warni jika aktif
            lw = max(2, w // 350)
            text_color = (0, 0, 0)
        else:
            color = (150, 150, 150) # Abu-abu jika di luar zona (diabaikan)
            lw = 1
            text_color = (255, 255, 255)

        cv2.rectangle(img_bgr, (x1, y1), (x2, y2), color, lw)
        
        if in_zone:
            # Corner accents
            cs = max(10, min(x2 - x1, y2 - y1) // 5)
            clw = max(3, lw + 1)
            for (cx, cy), dx, dy in [
                ((x1, y1),  1,  1), ((x2, y1), -1,  1),
                ((x1, y2),  1, -1), ((x2, y2), -1, -1),
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
    now = time.time()
    newly_saved = []

    with tracking_lock:
        # 1. Pantau barang yang masuk ke DALAM ZONA
        for d in detections:
            cx = (d["box"][0] + d["box"][2]) / 2
            cy = (d["box"][1] + d["box"][3]) / 2
            
            if ROI[0] <= cx <= ROI[1] and ROI[2] <= cy <= ROI[3]:
                kelas = d["kelas"]
                if kelas not in active_tracking:
                    active_tracking[kelas] = {"last_seen": now, "conf": d["confidence"]}
                else:
                    active_tracking[kelas]["last_seen"] = now
                    # Simpan confidence paling tinggi selama dia lewat
                    if d["confidence"] > active_tracking[kelas]["conf"]:
                        active_tracking[kelas]["conf"] = d["confidence"]
        
        # 2. Cek barang yang SUDAH KELUAR ZONA (Hilang > EXIT_DELAY detik)
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
                    "waktu": waktu_str,
                    "kelas": kelas,
                    "confidence": round(data["conf"] * 100, 1)
                })
                # Hapus dari tracking agar siap menerima barang kelas yang sama berikutnya
                del active_tracking[kelas]

    return newly_saved

# ── Zero-Latency Camera Stream ─────────────────────────────────────────────────
def pakai_picamera2(src) -> bool:
    """True kalau sumber ini harus dibaca lewat Picamera2 (Pi Camera di Raspberry Pi)."""
    # Hanya "Kamera 0" yang diarahkan ke Pi Camera. URL (DroidCam) dan indeks lain tetap OpenCV.
    if CAM_BACKEND == "opencv" or not PICAMERA2_ADA:
        return False
    if isinstance(src, str) or src != 0:
        return False
    if CAM_BACKEND == "picamera2":
        return True
    try:
        return len(Picamera2.global_camera_info()) > 0
    except Exception:
        return False

class CameraStream:
    def __init__(self, src):
        self.stopped = False
        self.picam   = None
        self.stream  = None
        self.ret     = False
        self.frame   = None

        if pakai_picamera2(src):
            try:
                self.picam = Picamera2(src)
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

        if self.picam is None:
            # OpenCV fallback (Webcam / USB / IP Cam / RPi V4L2)
            cam_sources = []
            if isinstance(src, int) or (isinstance(src, str) and src.isdigit()):
                idx = int(src)
                # Di Linux/Raspberry Pi, coba V4L2 backend dulu, lalu ANY, lalu indeks 0 & 1
                cam_sources = [(idx, cv2.CAP_V4L2), (idx, cv2.CAP_ANY)]
                if idx == 0:
                    cam_sources.extend([(1, cv2.CAP_V4L2), (1, cv2.CAP_ANY)])
            else:
                cam_sources = [(src, cv2.CAP_ANY)]

            for s, backend in cam_sources:
                try:
                    cap = cv2.VideoCapture(s, backend) if backend != cv2.CAP_ANY else cv2.VideoCapture(s)
                    if cap.isOpened():
                        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                        cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAM_WIDTH)
                        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAM_HEIGHT)
                        
                        # Warmup retry (hingga 15 percobaan / 1.5 detik) agar sensor V4L2/Raspberry Pi siap
                        for _ in range(15):
                            ret, frame = cap.read()
                            if ret and frame is not None:
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
                    print(f"Gagal mencoba sumber kamera {s}: {ex}")

            if not self.ret:
                print(f"Peringatan: Tidak dapat membaca frame dari sumber kamera {src}")

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

# ── Thread Kamera & YOLO ──────────────────────────────────────────────────────
def camera_loop():
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

    frame_count = 0
    last_deteksi   = []
    last_frame_bytes = None

    while is_running:
        ret, frame = cap.read()
        if not ret or frame is None:
            time.sleep(0.05)
            continue

        frame_count += 1

        # ── Frame Skip: hanya inferensi tiap FRAME_SKIP frame ──────────────
        # Di frame yang dilewati, pakai hasil deteksi sebelumnya (hemat CPU)
        if frame_count % FRAME_SKIP == 0:
            mulai = time.perf_counter()
            with model_lock:
                hasil = model.predict(frame.copy(), imgsz=IMGSZ,
                                      conf=current_conf, verbose=False)[0]
            ms = round((time.perf_counter() - mulai) * 1000)

            last_deteksi = [
                {
                    "kelas":      model.names[int(b.cls[0])],
                    "confidence": round(float(b.conf[0]), 3),
                    "box":        [round(float(v), 4) for v in b.xyxyn[0]],
                }
                for b in hasil.boxes
            ]

            newly_saved = process_tracking(last_deteksi)
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
            # Frame yang dilewati: tetap update gambar tanpa inferensi ulang
            if last_deteksi is not None:
                frame_bytes = draw_boxes_on_frame(frame.copy(), last_deteksi)
                with state_lock:
                    latest_frame = frame_bytes

    cap.stop()
    with state_lock:
        latest_frame  = None
        latest_result = {"detections": [], "ms": 0, "ts": 0,
                         "running": False, "newly_saved": []}
    push_sse(latest_result)

# ── Routes: Halaman ────────────────────────────────────────────────────────────
@app.get("/")
def index():
    return render_template("display.html")

# ── Routes: API Control ────────────────────────────────────────────────────────
@app.post("/api/start")
def start_cam():
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

# ── Routes: Riwayat ────────────────────────────────────────────────────────────
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
    buf = io.BytesIO(output.getvalue().encode("utf-8-sig"))  # utf-8-sig agar Excel terbaca
    filename = f"riwayat_deteksi_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return send_file(buf, mimetype="text/csv",
                     as_attachment=True, download_name=filename)

# ── Routes: Streaming ──────────────────────────────────────────────────────────
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

    resp = Response(event_stream(q), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @resp.call_on_close
    def remove_client():
        with sse_lock:
            if q in sse_clients:
                sse_clients.remove(q)
    return resp

@app.get("/video/stream")
def video_stream():
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
            time.sleep(0.05)
    return Response(generate(), mimetype="multipart/x-mixed-replace; boundary=frame")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, threaded=True)