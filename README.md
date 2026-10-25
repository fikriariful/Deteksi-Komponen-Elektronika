# ElectroCom61 — Sistem Deteksi Komponen Elektronika 🔬

<div align="center">

![Python](https://img.shields.io/badge/Python-3.10%2B-blue?style=for-the-badge&logo=python)
![Flask](https://img.shields.io/badge/Flask-3.x-black?style=for-the-badge&logo=flask)
![YOLOv8](https://img.shields.io/badge/YOLOv8n-Ultralytics-orange?style=for-the-badge)
![NCNN](https://img.shields.io/badge/NCNN-ARM%20Optimized-blueviolet?style=for-the-badge)
![Raspberry Pi](https://img.shields.io/badge/Raspberry%20Pi-4-red?style=for-the-badge&logo=raspberry-pi)

**Aplikasi web inspeksi real-time komponen elektronika pada konveyor menggunakan Raspberry Pi 4, Raspberry Pi Camera Rev 1.3 (OV5647), dan model AI YOLOv8n format NCNN.**

</div>

---

## 📋 Deskripsi Proyek

**ElectroCom61** adalah sistem inspeksi dan deteksi objek otomatis berbasis *computer vision* dan *edge AI* yang dirancang untuk mengenali **61 jenis komponen elektronika** secara *real-time*. Sistem ini diimplementasikan untuk konveyor/treadmill mini inspeksi komponen, di mana setiap komponen yang melintas di dalam **Zona Inspeksi (ROI)** akan diidentifikasi, diberi penanda visual, dan dicatat otomatis ke dalam database SQLite lokal.

Proyek ini merupakan bagian dari penelitian Tugas Akhir di **Universitas Riau**, Program Studi Teknik Informatika.

### 🌟 Fitur Utama & Optimasi Performa
- **Model Edge NCNN (Ultra-Ringan untuk ARM):** Menggunakan bobot model YOLOv8n yang diekspor khusus ke format NCNN (`model.ncnn.param` dan `model.ncnn.bin`), framework komputasi neural network yang dioptimalkan khusus untuk arsitektur CPU ARM Raspberry Pi 4.
- **Pemisahan Pipeline Multi-Threaded (Anti Stuttering / Bebas Lag):**
  1. *Thread Capture*: Membaca sensor kamera secara kontinu tanpa latensi antrean (*zero-lag bufferless*).
  2. *Thread Stream Rendering*: Merender visualisasi dan mengirim feed video MJPEG ke browser secara langsung tanpa pernah menunggu proses inferensi AI selesai. Video di browser mengalir mulus mengikuti kecepatan kamera.
  3. *Thread Inferensi*: Menjalankan prediksi model NCNN secara mandiri pada core CPU terpisah.
- **Zona Inspeksi Cerdas (ROI Debouncing):** Objek dideteksi di dalam kotak ROI kuning. Sistem mencatat ke database **tepat 1 kali** saat objek keluar dari zona inspeksi untuk mencegah duplikasi data, dengan mengambil nilai *confidence* tertinggi.
- **Penulisan Database Asinkron:** Operasi penyimpanan ke SQLite menggunakan thread pekerja terpisah (*queue worker*), menjaga agar operasi I/O microSD tidak membebani komputasi AI.
- **Dedicated Raspberry Pi Camera (CSI):** Terintegrasi langsung dengan modul sensor **Raspberry Pi Camera Rev 1.3 (OV5647)** via native `Picamera2` dan `libcamera`.
- **Mode Profiling Terintegrasi:** Dilengkapi parameter `PROFILE=1` untuk memantau FPS riil kamera, FPS stream, FPS inferensi, latensi per tahap (ms), suhu CPU, dan frekuensi ARM secara berkala tiap 5 detik.

---

## 🗂️ Struktur Folder Proyek

```text
electrocom61-app/
│
├── app.py                                   # Server backend Flask (multi-threaded pipeline & NCNN)
├── requirements.txt                         # Dependensi pustaka Python
├── README.md                                # Dokumentasi lengkap proyek
│
├── models/
│   ├── classes.txt                          # Daftar 61 nama kelas komponen elektronika
│   └── best_ncnn_model/                     # Model NCNN terlatih untuk Raspberry Pi
│       ├── metadata.yaml                    # Konfigurasi model & resolusi input (640x640)
│       ├── model.ncnn.param                 # Struktur layer neural network NCNN
│       └── model.ncnn.bin                   # Bobot biner model NCNN (12 MB)
│
├── templates/
│   └── display.html                         # Tampilan web dashboard monitor inspeksi
│
├── static/
│   ├── css/
│   │   └── display.css                      # Desain antarmuka dark-mode industrial
│   └── js/
│       └── display.js                       # Logika frontend: koneksi SSE & video stream
│
├── data/
│   └── history.db                           # Database SQLite riwayat deteksi komponen
│
└── YOLOv8n_ElectroCom61_NCNN_Training.ipynb # Notebook training & ekspor NCNN
```

---

## ⚙️ Arsitektur & Teknologi

| Komponen | Teknologi | Keterangan & Peran |
|---|---|---|
| **Backend Framework** | Python 3.10+ & Flask | Mengelola REST API, SSE telemetry, dan MJPEG video feed |
| **Object Detection Model** | YOLOv8n (Ultralytics) | Ekstraksi fitur dan prediksi bounding box 61 kelas |
| **Inference Engine** | NCNN (Tencent) | Framework inferensi khusus CPU ARM (tanpa butuh GPU) |
| **Computer Vision** | OpenCV (cv2) & NumPy | Pra-pemrosesan frame, rendering ROI, dan kompresi JPEG |
| **Camera Driver (CSI)** | Picamera2 & libcamera | Driver kamera ribbon cable Raspberry Pi |
| **Database** | SQLite3 (via Thread Queue) | Penyimpanan transaksi data komponen terverifikasi |
| **Target Hardware** | Raspberry Pi 4 Model B | Komputasi mandiri di sisi konveyor/edge |

---

## 🛠️ Panduan Instalasi & Menjalankan di Raspberry Pi

### Langkah 1: Kloning / Update Repository di Raspberry Pi

```bash
cd ~
git clone https://github.com/fikriariful/Deteksi-Komponen-Elektronika.git electrocom61-app
# Atau jika sudah ada:
cd ~/electrocom61-app
git pull
```

### Langkah 2: Pasang Paket Sistem & PyTorch Resmi Debian
Pada Raspberry Pi OS (Debian Bookworm / Trixie), pasang paket runtime resmi dari repositori Debian agar terbebas dari bug symbol `sbgemm_`:

```bash
sudo apt update
sudo apt install -y python3-torch python3-torchvision python3-picamera2 libopenblas-dev
```

### Langkah 3: Siapkan Virtual Environment
Buat virtual environment dengan opsi `--system-site-packages` agar dapat mengakses modul kamera native dan library Debian:

```bash
python3 -m venv --system-site-packages venv
source venv/bin/activate
```

### Langkah 4: Pasang Dependensi Python
```bash
pip install -r requirements.txt
```

---

## 🚀 Menjalankan Aplikasi

Pastikan virtual environment telah aktif (`source venv/bin/activate`), lalu jalankan:

### Mode Standar (Operasional Harian):
```bash
python3 app.py
```

### Mode Profiling (Monitoring Latensi, FPS & Suhu):
```bash
PROFILE=1 python3 app.py
```
*Pada mode ini, terminal akan mencetak statistik performa terstruktur setiap 5 detik (FPS kamera, FPS stream browser, FPS inferensi NCNN, latensi per tahap, jeda stream, dan suhu CPU).*

---

## 🌐 Akses Web Dashboard

Setelah server berjalan, buka peramban (browser) di laptop, PC, atau HP yang berada dalam satu jaringan WiFi/LAN:
- **`http://<IP-RASPBERRY-PI>:5000`**

1. Klik tombol **"▶ Mulai Inspeksi"** untuk memulai penangkapan kamera dan deteksi AI.
2. Letakkan komponen elektronika di atas konveyor berjalan melewati **Kotak Kuning (Zona Inspeksi)**.
3. Data komponen yang terdeteksi akan otomatis muncul di tabel dan tersimpan ke SQLite.
4. Klik **"Unduh CSV"** untuk mengekspor rekapitulasi data hasil inspeksi.

---

## 🔧 Variabel Konfigurasi (Opsional)

Aplikasi mendukung konfigurasi fleksibel via *environment variables*:

| Variabel | Default | Deskripsi |
|---|---|---|
| `PORT` | `5000` | Port HTTP server Flask |
| `CONF` | `0.60` | Ambang batas confidence minimum deteksi (60%) |
| `CAM_FPS` | `20` | Batas framerate kamera hardware Picamera2 (hemat CPU) |
| `CAM_BUFFERS`| `2` | Jumlah buffer hardware driver kamera |
| `CAM_EXPOSURE_US`| `0` | Shutter speed kamera (`0` = Auto; misal `8000` untuk 8 ms anti-blur) |
| `CAM_GAIN` | `2.5` | Analogue gain saat manual exposure aktif |
| `NCNN_THREADS` | `3` | Jumlah thread CPU untuk inferensi NCNN (menyisakan 1 core untuk web) |
| `PROFILE` | `0` | Aktifkan laporan berkala profiling terminal (`1` = Aktif) |

Contoh menjalankan dengan exposure cepat untuk objek bergerak di konveyor:
```bash
CAM_EXPOSURE_US=8000 CAM_GAIN=2.5 python3 app.py
```
