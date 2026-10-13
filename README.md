# ElectroCom61 — Sistem Deteksi Komponen Elektronika 🔬

<div align="center">

![Python](https://img.shields.io/badge/Python-3.10-blue?style=for-the-badge&logo=python)
![Flask](https://img.shields.io/badge/Flask-3.x-black?style=for-the-badge&logo=flask)
![YOLOv8](https://img.shields.io/badge/YOLOv8n-Ultralytics-orange?style=for-the-badge)
![ONNX](https://img.shields.io/badge/ONNX-Runtime-lightgrey?style=for-the-badge&logo=onnx)
![Raspberry Pi](https://img.shields.io/badge/Raspberry%20Pi-4-red?style=for-the-badge&logo=raspberry-pi)

**Aplikasi berbasis web untuk mendeteksi dan mencatat komponen elektronika secara real-time menggunakan kamera dan model AI YOLOv8n (ONNX Runtime).**

</div>

---

## 📋 Deskripsi Proyek

**ElectroCom61** adalah sistem inspeksi dan deteksi objek otomatis berbasis *computer vision* dan *edge AI* yang dirancang untuk mengenali **61 jenis komponen elektronika** secara *real-time*. Sistem ini diimplementasikan untuk konveyor/treadmill inspeksi komponen di laboratorium, di mana setiap komponen yang melintas di dalam **Zona Inspeksi (ROI)** akan diidentifikasi, diberi penanda visual, dan dicatat otomatis ke dalam database lokal.

Proyek ini merupakan bagian dari penelitian Tugas Akhir di **Universitas Riau**, Program Studi Teknik Informatika.

### 🌟 Fitur Utama
- **Model Edge AI Ringan:** Menggunakan arsitektur YOLOv8n yang diekspor ke format ONNX (ONNX Runtime CPU), dioptimalkan untuk perangkat edge tanpa GPU.
- **Zona Inspeksi Cerdas (ROI Debouncing):** Deteksi komponen difokuskan pada area tengah (ROI kuning). Pencatatan ke database dilakukan **tepat satu kali** saat komponen keluar dari zona inspeksi untuk mencegah duplikasi data.
- **Dukungan Multi-Kamera Fleksibel:** Mendukung modul **Raspberry Pi Camera Rev 1.3 (CSI via Picamera2)**, Webcam USB eksternal, hingga IP Camera / DroidCam via WiFi.
- **Streaming & Telemetri Real-Time:** Video MJPEG berlatensi rendah serta pembaruan data telemetri (kelas, akurasi, waktu inferensi) menggunakan *Server-Sent Events* (SSE).
- **Penyimpanan & Ekspor Data:** Riwayat deteksi tersimpan otomatis di SQLite lokal dan dapat diunduh langsung dalam format **CSV / Excel**.
- **Optimasi Khusus Edge Device (Raspberry Pi 4):** Dilengkapi *warmup retries*, *frame skipping*, dan *thread-safe bufferless streaming* agar konsumsi CPU tetap rendah dan stabil tanpa lag.

---

## 🗂️ Struktur Folder

```
electrocom61-app/
│
├── app.py                          # Server backend Flask, pipeline YOLO ONNX, & streaming
├── requirements.txt                # Dependensi pustaka Python
├── CATATAN_GITHUB.md               # Panduan ringkas operasional Git & deployment
│
├── models/
│   └── best.onnx                   # Model YOLOv8n ONNX terlatih (mAP50: 91.64%)
│
├── templates/
│   └── display.html                # Tampilan web dashboard monitor inspeksi
│
├── static/
│   ├── css/
│   │   └── display.css             # Tema antarmuka dark-mode industrial
│   └── js/
│       └── display.js              # Logika frontend: koneksi SSE, kontrol kamera, & CSV
│
├── data/
│   └── history.db                  # Database SQLite riwayat deteksi komponen
│
└── YOLOv8n_ElectroCom61_YOLOv8n_Done.ipynb   # Notebook training & evaluasi model
```

---

## ⚙️ Arsitektur & Teknologi

| Komponen | Teknologi | Keterangan & Peran |
|---|---|---|
| **Backend Framework** | Python 3.10+ & Flask | Mengelola REST API, SSE stream, dan MJPEG video feed |
| **Object Detection Model** | YOLOv8n (Ultralytics) | Ekstraksi fitur dan prediksi bounding box 61 kelas |
| **Inference Engine** | ONNX Runtime | Inferensi ringan CPU-only tanpa dependensi CUDA |
| **Computer Vision** | OpenCV (cv2) & NumPy | Pra-pemrosesan frame, rendering ROI, dan kompresi JPEG |
| **Camera Driver (CSI)** | Picamera2 & libcamera | Driver antarmuka kamera kabel pita Raspberry Pi |
| **Database** | SQLite3 | Penyimpanan transaksi data komponen terverifikasi |
| **Frontend** | HTML5, CSS3, Vanilla JS | Antarmuka responsif tanpa dependensi framework berat |
| **Target Hardware** | Raspberry Pi 4 Model B (8GB) | Komputasi mandiri di sisi konveyor/edge |

---

## 📊 Hasil Evaluasi Model

Model dilatih menggunakan dataset **ElectroCom61** (2.121 citra beranotasi mencakup 61 kelas komponen elektronika):

| Metrik Evaluasi | Validation Set | Test Set | Keterangan |
|---|---|---|---|
| **mAP@0.5** | **91.64%** | **89.65%** | Akurasi deteksi pada IoU 0.50 |
| **Precision** | **87.23%** | **84.95%** | Tingkat ketepatan identifikasi kelas |
| **Recall** | **89.74%** | **84.33%** | Kemampuan mendeteksi seluruh objek riil |
| **F1-Score** | **88.47%** | **84.64%** | Keseimbangan harmonis Precision-Recall |
| **mAP@0.5:0.95** | **58.82%** | **59.16%** | Evaluasi komprehensif pada berbagai ambang batas IoU |

**Parameter Training:**
- Base Model: YOLOv8 Nano (`yolov8n.pt`)
- Epochs: 50 (Early Stopping Patience: 10)
- Image Resolution (`imgsz`): 640×640
- Batch Size: 8
- Optimizer: Adam (`lr0 = 0.001`)

---

## 🛠️ Panduan Instalasi & Menjalankan Aplikasi

### Persyaratan Sistem
- Python 3.10 atau lebih baru
- Git
- Modul Kamera (Raspberry Pi Camera Rev 1.3 / Webcam USB / IP Camera)

### 1. Kloning Repository

```bash
git clone https://github.com/fikriariful/Deteksi-Komponen-Elektronika.git
cd Deteksi-Komponen-Elektronika
```

### 2. Konfigurasi Virtual Environment

```bash
# Windows
python -m venv venv
.\venv\Scripts\activate

# Linux / Raspberry Pi OS
sudo apt update
sudo apt install -y python3-venv python3-pip
python3 -m venv venv
source venv/bin/activate
```

### 3. Instalasi Dependensi Pustaka

```bash
pip install -r requirements.txt
```

> 💡 **Khusus Raspberry Pi OS (Debian/Linux):** Pasang dependensi rendering sistem:
> ```bash
> sudo apt update
> sudo apt install -y libgl1 libglib2.0-0 libsm6 libxext6 v4l-utils
> ```

### 4. Menjalankan Server

```bash
python app.py
```

Buka peramban (browser) dan akses:
- Di perangkat lokal: **`http://localhost:5000`**
- Dari PC/Laptop klien: **`http://<IP-RASPBERRY-PI>:5000`**

---

## 🍓 Panduan Konfigurasi Raspberry Pi 4 & Kamera Rev 1.3

### 1. Konfigurasi Kamera Ribbon CSI (Rev 1.3)
Agar sensor kamera pita terdeteksi optimal oleh subsistem Linux:
```bash
sudo raspi-config
```
1. Masuk ke menu **Interface Options** → **Legacy Camera** → Pilih **Enable** (atau aktifkan modul Camera).
2. Terapkan dan restart sistem:
   ```bash
   sudo reboot
   ```
3. Verifikasi ketersediaan node kamera:
   ```bash
   ls /dev/video*
   ```

### 2. Tips Efisiensi MicroSD & Memori Raspberry Pi (16GB)
Hindari error `No space left on device` dengan beberapa langkah penting:
- Jangan menyalin folder `venv` dari Windows ke Raspberry Pi; selalu buat venv baru di dalam Pi.
- Bersihkan cache pip dan apt setelah instalasi:
  ```bash
  pip cache purge
  sudo apt clean
  ```
- Install PyTorch versi CPU-only bila dibutuhkan tanpa mengunduh paket CUDA raksasa:
  ```bash
  pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
  ```

### 3. Eksekusi Optimal (Tanpa Lag)
Di dalam `app.py`, pengaturan berikut telah diterapkan secara default:
- `IMGSZ = 320` (resolusi inferensi diperkecil untuk menghemat CPU ~4×)
- `FRAME_SKIP = 3` (inferensi hanya dijalankan tiap 3 frame)
- `JPEG_QUALITY = 65` (menghemat bandwidth streaming WiFi)

Cukup jalankan:
```bash
python3 app.py
```

---

## 📱 Panduan Penggunaan Antarmuka Web

1. **Akses Dashboard:** Buka alamat URL aplikasi melalui browser.
2. **Pilih Sumber Kamera:**
   - `Kamera Raspberry Pi / USB (Kamera 0)`: Kamera pita CSI atau webcam utama.
   - `Kamera USB Eksternal (Kamera 1)`: Kamera USB kedua.
   - `IP Camera / DroidCam WiFi`: Masukkan URL stream video HTTP (misalnya dari aplikasi DroidCam).
3. **Mulai Deteksi:** Klik **"▶ Mulai Deteksi"** untuk mengaktifkan pipeline AI dan memulai sesi pencatatan baru.
4. **Inspeksi Komponen:** Lewatkan komponen elektronika melalui **Kotak Zona Inspeksi (Kuning)**. Komponen akan dicatat otomatis setelah melintasi zona.
5. **Sesuaikan Sensitivitas:** Atur slider ambang batas *Confidence* (disarankan 50%–65%).
6. **Ekspor Data:** Klik **"⏹ Hentikan"** untuk mengakhiri sesi deteksi dan mengunduh laporan rekapitulasi data dalam format **CSV / Excel**.

---

## 📦 Daftar 61 Komponen Elektronika Terdeteksi

Model mampu mengidentifikasi 61 varian komponen elektronika yang mencakup:
- **Kapasitor:** Ceramic Capacitor, Electrolytic Capacitor, Film Capacitor, Tantalum Capacitor, SMD Capacitor, dll.
- **Resistor:** Carbon Film Resistor, Metal Film Resistor, Variable Resistor (Potentiometer), Cement Resistor, LDR, dll.
- **Semikonduktor & Komponen Aktif:** Transistor (BJT, MOSFET), Diode, Zener Diode, Bridge Rectifier, LED berbagai warna.
- **Sensor:** Sensor Suhu, Sensor Ultrasonik, Sensor Cahaya, PIR Motion Sensor, Water Sensor, IR Flame Sensor.
- **Modul & IC:** IC DIP/SOP (Timer 555, Op-Amp), Motor Driver L298N, Relay Module, Step-down Buck Converter, Modul Bluetooth, ESP/Arduino modul pendukung.

---

## 📚 Referensi Ilmiah & Pustaka

1. **Jocher, G., Chaurasia, A., & Qiu, J.** (2023). *Ultralytics YOLOv8* (Version 8.0.0) [Software]. GitHub. https://github.com/ultralytics/ultralytics
2. **Redmon, J., Divvala, S., Girshick, R., & Farhadi, A.** (2016). *You Only Look Once: Unified, Real-Time Object Detection*. In Proceedings of the IEEE Conference on Computer Vision and Pattern Recognition (CVPR), pp. 779-788.
3. **Bai, J. et al.** (2019). *ONNX: Open Neural Network Exchange*. GitHub Repository. https://github.com/onnx/onnx
4. **Microsoft.** (2021). *ONNX Runtime: Cross-platform, high performance ML inferencing and training accelerator*. https://onnxruntime.ai
5. **Bradski, G.** (2000). *The OpenCV Library*. Dr. Dobb's Journal of Software Tools, 25(11), 120-125.
6. **Raspberry Pi Foundation.** (2022). *Picamera2 Library: Official Python interface for libcamera*. Raspberry Pi Documentation. https://datasheets.raspberrypi.com/picamera2/picamera2-python-api.pdf
7. **Grinberg, M.** (2018). *Flask Web Development: Developing Web Applications with Python* (2nd ed.). O'Reilly Media.
8. **Kaggle Datasets.** (2023). *ElectroCom-61: A Multiclass Dataset for Detection of Electronic Components*. https://www.kaggle.com/datasets

---

## 👤 Informasi Peneliti & Pengembang

| Atribut | Keterangan |
|---|---|
| **Nama Peneliti** | Ariful Fikri |
| **NIM** | 2307110474 |
| **Program Studi** | Teknik Informatika |
| **Fakultas** | Fakultas Teknik |
| **Institusi** | Universitas Riau |
| **Tahun** | 2026 |

---

## 📝 Lisensi

Proyek ini dikembangkan untuk kebutuhan akademis dan penelitian Tugas Akhir di Program Studi Teknik Informatika, Universitas Riau.

<div align="center">
  <b>ElectroCom61</b> — Sistem Deteksi Komponen Elektronika Real-Time<br>
  Universitas Riau © 2026
</div>
