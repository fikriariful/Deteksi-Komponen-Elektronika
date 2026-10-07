# ElectroCom61 — Sistem Deteksi Komponen Elektronika 🔬

<div align="center">

![Python](https://img.shields.io/badge/Python-3.10-blue?style=for-the-badge&logo=python)
![Flask](https://img.shields.io/badge/Flask-3.x-black?style=for-the-badge&logo=flask)
![YOLOv8](https://img.shields.io/badge/YOLOv8n-Ultralytics-orange?style=for-the-badge)
![ONNX](https://img.shields.io/badge/ONNX-Runtime-lightgrey?style=for-the-badge&logo=onnx)
![Raspberry Pi](https://img.shields.io/badge/Raspberry%20Pi-4-red?style=for-the-badge&logo=raspberry-pi)

**Aplikasi berbasis web untuk mendeteksi dan mencatat komponen elektronika secara real-time menggunakan kamera dan model AI YOLOv8n.**

</div>

---

## 📋 Deskripsi Proyek

**ElectroCom61** adalah sistem deteksi objek berbasis *computer vision* yang dirancang untuk mengenali **61 jenis komponen elektronika** secara otomatis melalui kamera. Sistem ini ditujukan untuk digunakan pada konveyor treadmill di laboratorium elektronika, di mana setiap komponen yang melintas akan dideteksi, diidentifikasi, dan dicatat secara otomatis ke dalam database.

Proyek ini merupakan bagian dari penelitian Tugas Akhir di **Universitas Riau**, Program Studi Teknik Elektro.

### Fitur Utama
- ✅ Deteksi real-time menggunakan YOLOv8n (model ringan, cocok untuk Raspberry Pi 4)
- ✅ **Zona Inspeksi (ROI)** — hanya mencatat komponen yang melintas di dalam zona
- ✅ Riwayat deteksi tersimpan di database SQLite
- ✅ Export data ke file **CSV / Excel**
- ✅ Streaming video langsung di browser (MJPEG)
- ✅ Mendukung kamera USB, Webcam laptop, dan **IP Camera (DroidCam)**
- ✅ Dioptimalkan untuk **Raspberry Pi 4** dengan ONNX Runtime

---

## 🗂️ Struktur Folder

```
electrocom61-app/
│
├── app.py                          # Server utama Flask + YOLO inference
├── requirements.txt                # Daftar library Python yang dibutuhkan
│
├── models/
│   └── best.onnx                   # Model YOLOv8n yang sudah dilatih & di-export ke ONNX
│
├── templates/
│   └── display.html                # Halaman antarmuka web
│
├── static/
│   ├── css/
│   │   └── display.css             # Stylesheet antarmuka
│   └── js/
│       └── display.js              # Logic frontend (SSE, riwayat, export CSV)
│
├── data/
│   └── history.db                  # Database SQLite (dibuat otomatis)
│
└── YOLOv8n_ElectroCom61_YOLOv8n_Done.ipynb   # Notebook training model
```

---

## ⚙️ Teknologi yang Digunakan

| Komponen | Teknologi | Keterangan |
|---|---|---|
| **Backend** | Python + Flask | Server web & API |
| **AI / Deteksi** | YOLOv8n (Ultralytics) | Model deteksi objek |
| **Format Model** | ONNX Runtime | Ringan untuk CPU/Raspberry Pi |
| **Kamera** | OpenCV | Capture & stream video |
| **Database** | SQLite | Penyimpanan riwayat deteksi |
| **Frontend** | HTML + CSS + JS | Antarmuka pengguna real-time |
| **Hardware** | Raspberry Pi 4 (8GB RAM) | Perangkat edge computing |
| **Kamera** | ArduCam / DroidCam | Sumber input video |

---

## 📊 Hasil Training Model

Model dilatih menggunakan dataset **ElectroCom61** dari Kaggle (2.121 gambar, 61 kelas komponen elektronika).

| Metrik | Validation | Test Set |
|---|---|---|
| **mAP50** | **91.64%** | **89.65%** |
| Precision | 87.23% | 84.95% |
| Recall | 89.74% | 84.33% |
| F1-Score | 88.47% | 84.64% |
| mAP50-95 | 58.82% | 59.16% |

**Konfigurasi Training:**
- Model: YOLOv8n (Nano) — dioptimalkan untuk edge device
- Epoch: 50 (dengan early stopping patience=10)
- Image Size: 640×640
- Batch Size: 8
- Optimizer: Adam (lr=0.001)
- GPU: NVIDIA GTX 1650 (4GB VRAM)

---

## 🛠️ Instalasi dan Menjalankan Aplikasi

### Persyaratan Sistem
- Python 3.10+
- Git
- Kamera (USB / Webcam / IP Camera)

### 1. Clone Repository

```bash
git clone https://github.com/USERNAME/electrocom61-app.git
cd electrocom61-app
```

### 2. Buat Virtual Environment

```bash
# Windows
python -m venv venv
.\venv\Scripts\activate

# Linux / Raspberry Pi
python3 -m venv venv
source venv/bin/activate
```

### 3. Install Library

```bash
pip install -r requirements.txt
```

> ⚠️ **Khusus Raspberry Pi (Linux):** Install library sistem dulu sebelum `pip install`:
> ```bash
> sudo apt update
> sudo apt install -y libgl1 libglib2.0-0 libsm6 libxext6
> ```

### 4. Jalankan Aplikasi

```bash
python app.py
```

Buka browser dan akses: **`http://localhost:5000`**

Jika dibuka dari perangkat lain (misal: buka Raspberry Pi dari laptop):
**`http://<IP-RASPBERRY-PI>:5000`**

---

## 🍓 Panduan Khusus Raspberry Pi 4

### Koneksi Remote via SSH

```bash
# Cari IP Raspberry Pi
hostname -I

# Koneksi dari laptop Windows (PowerShell)
ssh pi@192.168.x.x
```

### Install di Raspberry Pi

```bash
# Install library sistem
sudo apt update
sudo apt install -y libgl1 libglib2.0-0 libsm6 libxext6

# Clone dan install aplikasi
git clone https://github.com/USERNAME/electrocom61-app.git
cd electrocom61-app
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Jalankan
python3 app.py
```

### Optimasi Performa di Raspberry Pi

Aplikasi sudah dikonfigurasi untuk berjalan ringan di Raspberry Pi 4:

```bash
# Konfigurasi default yang sudah dioptimalkan:
# - IMGSZ=320    (resolusi inferensi lebih kecil → 4x lebih cepat)
# - FRAME_SKIP=3 (inferensi hanya tiap 3 frame → hemat CPU)
# - JPEG_QUALITY=65 (kualitas streaming lebih ringan)

python3 app.py

# Atau custom manual:
IMGSZ=320 FRAME_SKIP=3 python3 app.py
```

---

## 📱 Cara Penggunaan Aplikasi

1. **Buka browser** dan akses `http://localhost:5000`
2. **Pilih Sumber Kamera** dari dropdown (Webcam / USB / IP Camera)
3. Klik **"Mulai Deteksi"** — kamera akan aktif dan riwayat baru dimulai
4. **Arahkan komponen elektronika** ke dalam **Kotak Zona Inspeksi** (kotak kuning di layar)
5. Komponen yang melintas dalam zona akan **otomatis dicatat** ke riwayat saat keluar zona
6. Klik **"Hentikan"** — sistem akan menawarkan **Download file CSV** riwayat deteksi

### Tips Penggunaan
- Atur slider **Sensitivitas** di antara 50-65% untuk hasil terbaik
- Pastikan komponen diarahkan tepat ke dalam kotak kuning
- Gunakan pencahayaan yang cukup terang untuk meningkatkan akurasi

---

## 📦 Daftar Komponen yang Dapat Dideteksi

Model dapat mendeteksi **61 jenis komponen elektronika**, diantaranya:

| Kategori | Contoh Komponen |
|---|---|
| Kapasitor | Film-Capacitor, MLC-Capacitor, Electrolytic-Capacitor |
| Resistor | Carbon-Resistor, Metal-Film-Resistor |
| Sensor | Water-Sensor, IR-Sensor, Temperature-Sensor |
| Modul | Motor-Driver, Relay-Module, Bluetooth-Module |
| Komponen Aktif | Transistor, Diode, LED |
| Dan banyak lagi... | *(Total 61 kelas)* |

---

## 📚 Referensi

1. **Redmon, J., & Farhadi, A.** (2018). *YOLOv3: An Incremental Improvement*. arXiv:1804.02767.

2. **Jocher, G., et al.** (2023). *Ultralytics YOLOv8*. GitHub. https://github.com/ultralytics/ultralytics

3. **ONNX Community** (2023). *ONNX Runtime: Cross-platform, High Performance ML Inferencing*. https://onnxruntime.ai/

4. **ElectroCom61 Dataset** — Kaggle. *ElectroCom61: A Multiclass Dataset for Detection of Electronic Components*. https://www.kaggle.com/datasets/

5. **Raspberry Pi Foundation** (2023). *Raspberry Pi 4 Model B Datasheet*. https://www.raspberrypi.com/products/raspberry-pi-4-model-b/

6. **Bradski, G.** (2000). *The OpenCV Library*. Dr. Dobb's Journal of Software Tools.

7. **Pallets Projects** (2023). *Flask — A lightweight WSGI web application framework*. https://flask.palletsprojects.com/

---

## 👤 Tentang Proyek

| | |
|---|---|
| **Nama** | *(Ariful Fikri)* |
| **NIM** | *(2307110474)* |
| **Program Studi** | Teknik Informatika |
| **Universitas** | Universitas Riau |
| **Tahun** | 2026 |

---

## 📝 Lisensi

Proyek ini dibuat untuk keperluan akademis Tugas Akhir Universitas Riau.

---

<div align="center">
  <b>ElectroCom61</b> — Sistem Deteksi Komponen Elektronika Real-Time<br>
  Universitas Riau © 2026
</div>
