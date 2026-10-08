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

Proyek ini merupakan bagian dari penelitian Tugas Akhir di **Universitas Riau**, Program Studi Teknik Informatika.

### Fitur Utama
- ✅ Deteksi real-time menggunakan YOLOv8n (model ringan, cocok untuk Raspberry Pi 4)
- ✅ **Zona Inspeksi (ROI)** — hanya mencatat komponen yang melintas di dalam zona
- ✅ Riwayat deteksi tersimpan di database SQLite
- ✅ Export data ke file **CSV / Excel**
- ✅ Streaming video langsung di browser (MJPEG)
- ✅ Mendukung **Raspberry Pi Camera Module Rev 1.3 (CSI)**, USB Webcam, dan **IP Camera (DroidCam)**
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
| **Modul Kamera** | Raspberry Pi Camera Rev 1.3 (CSI) / USB / IP Cam | Sumber input video utama |

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
- Kamera (Raspberry Pi Camera Rev 1.3 / USB Webcam / IP Camera)

### 1. Clone Repository

```bash
git clone https://github.com/fikriariful/Deteksi-Komponen-Elektronika.git
cd Deteksi-Komponen-Elektronika
```

### 2. Buat Virtual Environment

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

### 3. Install Library

```bash
pip install -r requirements.txt
```

> ⚠️ **Khusus Raspberry Pi (Linux):** Pastikan library pendukung OpenCV sudah terinstall:
> ```bash
> sudo apt update
> sudo apt install -y libgl1 libglib2.0-0 libsm6 libxext6 v4l-utils
> ```

### 4. Jalankan Aplikasi

```bash
python app.py
```

Buka browser dan akses: **`http://localhost:5000`**

Jika dibuka dari laptop (Raspberry Pi terhubung WiFi yang sama):
**`http://<IP-RASPBERRY-PI>:5000`**

---

## 🍓 Panduan Khusus Raspberry Pi 4 & Kamera Rev 1.3

### 1. Pengaturan Kamera Raspberry Pi Rev 1.3 (CSI Ribbon Cable)
Kamera Raspberry Pi Rev 1.3 terhubung melalui port CSI kabel pita. Agar terdeteksi oleh OpenCV sebagai `/dev/video0`:

1. Buka konfigurasi Raspberry Pi:
   ```bash
   sudo raspi-config
   ```
2. Masuk ke **Interface Options** -> **Legacy Camera** -> Pilih **Enable**.
3. Reboot Raspberry Pi:
   ```bash
   sudo reboot
   ```
4. Cek apakah kamera sudah terbaca oleh sistem:
   ```bash
   ls /dev/video*
   ```
   *(Jika muncul `/dev/video0`, berarti kamera Rev 1.3 sudah siap digunakan).*

---

### 2. Persiapan dan Instalasi di Raspberry Pi OS (Penting untuk SD Card 16GB)

Karena memori Raspberry Pi (MicroSD 16GB) sangat terbatas, ikuti langkah ini dengan hati-hati agar tidak terjadi error `No space left on device` atau lag yang parah.

#### A. Persiapan Folder di PC/Laptop (Sebelum dipindah ke Pi)
Jika Anda memindahkan folder secara manual (via Flashdisk), pastikan Anda **MENGHAPUS** folder/file berikut agar memori Pi tidak penuh:
- ❌ Hapus folder `venv` (Milik Windows, tidak bisa dipakai di Pi dan memakan ~1GB)
- ❌ Hapus folder `.git` (Jika tidak butuh riwayat git)
- ❌ Hapus file `.ipynb` (File training, tidak dipakai untuk menjalankan aplikasi)

#### B. Optimasi OS Raspberry Pi
Sangat disarankan untuk **TIDAK** menjalankan aplikasi ini melalui Visual Studio Code (VSCode) di Raspberry Pi karena sangat memakan RAM. Hapus VSCode dan gunakan terminal bawaan (LXTerminal):
```bash
sudo apt remove --purge code -y
sudo apt autoremove -y
rm -rf ~/.vscode ~/.config/Code ~/.vscode-shared
```

#### C. Proses Instalasi (Di Terminal Raspberry Pi)
Buka terminal bawaan (`Ctrl + Alt + T`), masuk ke folder aplikasi Anda, lalu jalankan perintah berikut:

```bash
# 1. Update & Install dependency sistem untuk OpenCV
sudo apt update
sudo apt install -y python3-venv python3-pip libgl1 libglib2.0-0 libsm6 libxext6 v4l-utils

# 2. Buat dan aktifkan virtual environment (Wajib!)
python3 -m venv venv
source venv/bin/activate

# 3. Bersihkan cache agar ruang penyimpanan lega
pip cache purge
sudo apt clean

# 4. INSTALL PYTORCH CPU-ONLY (SANGAT PENTING!)
# Langkah ini mencegah download file CUDA/NVIDIA raksasa yang akan membuat memori Pi penuh seketika.
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu

# 5. Install sisa library
pip install -r requirements.txt
```

---

### 3. Optimasi Performa di Raspberry Pi

Aplikasi sudah dikonfigurasi agar berjalan ringan di Raspberry Pi 4 tanpa lag:

```bash
# Konfigurasi default yang sudah dioptimalkan:
# - IMGSZ=320        (resolusi inferensi lebih kecil → 4x lebih cepat)
# - FRAME_SKIP=3     (inferensi hanya tiap 3 frame → hemat CPU)
# - JPEG_QUALITY=65  (kualitas streaming lebih ringan)

python3 app.py

# Atau jalankan dengan custom environment variable:
IMGSZ=320 FRAME_SKIP=3 python3 app.py
```

---

## 📱 Cara Penggunaan Aplikasi

1. **Buka browser** dan akses `http://localhost:5000`
2. **Pilih Sumber Kamera** dari dropdown (Default `0` untuk RPi Camera Rev 1.3 / Webcam)
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
5. **Raspberry Pi Foundation** (2023). *Raspberry Pi 4 Model B Datasheet & Camera Module Setup*. https://www.raspberrypi.com/documentation/
6. **Bradski, G.** (2000). *The OpenCV Library*. Dr. Dobb's Journal of Software Tools.
7. **Pallets Projects** (2023). *Flask — A lightweight WSGI web application framework*. https://flask.palletsprojects.com/

---

## 👤 Tentang Proyek

| | |
|---|---|
| **Nama** | Ariful Fikri |
| **NIM** | 2307110474 |
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
