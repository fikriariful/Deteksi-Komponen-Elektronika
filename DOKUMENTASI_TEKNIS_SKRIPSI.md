# DOKUMENTASI TEKNIS & PANDUAN KOMPREHENSIF SKRIPSI
# SISTEM DETEKSI KOMPONEN ELEKTRONIKA BERBASIS EDGE AI (ELECTROCOM61)

Dokumen ini disusun sebagai catatan teknis lengkap untuk persiapan sidang/ujian skripsi, mencakup arsitektur sistem, alur pemrosesan citra, matematika & algoritma tracking, optimasi hardware Raspberry Pi, hingga daftar tanya-jawab kritis dari dosen penguji.

---

## DAFTAR ISI
1. [Ringkasan Eksekutif Proyek](#1-ringkasan-eksekutif-proyek)
2. [Spesifikasi Hardware & Lingkungan Eksekusi](#2-spesifikasi-hardware--lingkungan-eksekusi)
3. [Model AI & Algoritma Deteksi Objek (YOLOv8n + NCNN)](#3-model-ai--algoritma-deteksi-objek-yolov8n--ncnn)
4. [Arsitektur Multi-Threaded Decoupled (Solusi Video Anti-Lag)](#4-arsitektur-multi-threaded-decoupled-solusi-video-anti-lag)
5. [Logika ROI (Region of Interest) & Debouncing Tracking](#5-logika-roi-region-of-interest--debouncing-tracking)
6. [Mekanisme Streaming Video (MJPEG) & Telemetri (SSE)](#6-mekanisme-streaming-video-mjpeg--telemetri-sse)
7. [Daftar Library, Teknologi, & Fungsinya](#7-daftar-library-teknologi--fungsinya)
8. [Tanya Jawab Kritis Ujian Skripsi (Bank Pertanyaan Dosen Penguji)](#8-tanya-jawab-kritis-ujian-skripsi-bank-pertanyaan-dosen-penguji)

---

## 1. RINGKASAN EKSEKUTIF PROYEK

* **Nama Sistem:** ElectroCom61 — Sistem Deteksi & Pencatatan Komponen Elektronika Otomatis.
* **Tujuan Utama:** Mendeteksi, mengidentifikasi 61 jenis komponen elektronika di atas konveyor mini secara real-time, menampilkan visualisasi inspeksi pada antarmuka web, dan mencatat riwayat komponen yang lolos inspeksi ke database lokal SQLite tanpa duplikasi data.
* **Target Komputasi:** Edge Device (Raspberry Pi 4 Model B) tanpa Graphic Processing Unit (GPU) eksternal.
* **Sensor Citra:** Raspberry Pi Camera Module Rev 1.3 (Sensor OmniVision OV5647, antarmuka CSI Ribbon Cable).

---

## 2. SPESIFIKASI HARDWARE & LINGKUNGAN EKSEKUSI

| Komponen | Spesifikasi Teknis | Alasan Pemilihan |
|---|---|---|
| **Papan Komputasi** | Raspberry Pi 4 Model B (Broadcom BCM2711, Quad-core Cortex-A72 ARM v8 64-bit @ 1.5 GHz) | Standar industri untuk edge computing berdaya rendah (low power), hemat energi, portabel di sisi konveyor. |
| **Kamera** | Raspberry Pi Camera Rev 1.3 (5 Megapixel, Sensor OV5647, MIPI CSI-2) | Antarmuka pita CSI bypass port USB, berkomunikasi langsung via jalur internal SoC Broadcom, meminimalkan latensi capture dibanding webcam USB biasa. |
| **Sistem Operasi** | Linux Debian 13 (Trixie) / Raspberry Pi OS 64-bit | Arsitektur 64-bit (aarch64) wajib digunakan untuk instruksi komputasi vektor ARM NEON pada inferensi neural network. |
| **Aktuator** | Mini Conveyor Belt | Mensimulasikan lini perakitan pabrik / sistem inspeksi manufaktur PCB otomatis. |

---

## 3. MODEL AI & ALGORITMA DETEKSI OBJEK (YOLOv8n + NCNN)

### 3.1. Arsitektur Model YOLOv8n
Model deteksi yang digunakan adalah **YOLOv8 Nano (`yolov8n`)**, varian paling ringan dari keluarga YOLOv8 (You Only Look Once versi 8) yang dikembangkan oleh Ultralytics.
* **Backbone:** CSPDarknet53 yang dimodifikasi dengan modul C2f (Cross-Stage Partial with 2 convolutions), berfungsi mengekstrak fitur multi-skala dari citra komponen.
* **Neck:** Kombinasi PANet (Path Aggregation Network) dan FPN (Feature Pyramid Network) untuk menggabungkan fitur level rendah (tekstur, sudut, kaki komponen) dan level tinggi (bentuk utuh IC, resistor, kapasitor).
* **Head:** Anchor-free detection head yang memisahkan tugas klasifikasi (*classification loss*: VFL / BCE) dan regresi bounding box (*bounding box regression loss*: CIoU + DFL).

### 3.2. Kenapa Format NCNN (Bukan PyTorch .pt atau ONNX)?
* **Masalah PyTorch (`.pt`):** Format `.pt` membutuhkan dependensi PyTorch lengkap (~500 MB RAM) dan dirancang untuk pelatihan di GPU. Sangat lambat dan membebani RAM Raspberry Pi jika dipakai untuk inferensi.
* **Masalah ONNX Runtime:** Pada Raspberry Pi 4, ONNX runtime menggunakan backend CPU generik yang sering kali tidak memanfaatkan seluruh akselerasi hardware ARM NEON secara optimal, menyebabkan latensi per frame ~350–500 ms (hanya 2–3 FPS) dan memblokir thread video.
* **Keunggulan NCNN (Tencent):**
  1. Didesain secara khusus dari nol untuk prosesor berarsitektur **ARM (Cortex-A)**.
  2. Menggunakan instruksi assembly **ARM NEON SIMD** (Single Instruction, Multiple Data) untuk operasi perkalian matriks (GEMM).
  3. Memiliki struktur memori sangat kecil tanpa ketergantungan library pihak ketiga yang rumit.
  4. Format NCNN terdiri dari 2 file utama:
     * `model.ncnn.param`: File teks berisi struktur graf topologi neural network.
     * `model.ncnn.bin`: File biner berisi bobot (weights) float32 terkompresi (~12 MB).
     * `metadata.yaml`: Berisi resolusi input baku (`imgsz: [640, 640]`) dan nama 61 kelas.

---

## 4. ARSITEKTUR MULTI-THREADED DECOUPLED (SOLUSI VIDEO ANTI-LAG)

### 4.1. Masalah pada Arsitektur Lama (Single Loop)
Pada kode awal, eksekusi dilakukan dalam satu loop berurutan:
$$\text{Ambil Frame Kamera} \longrightarrow \text{Inferensi Model (300 ms)} \longrightarrow \text{Gambar Box} \longrightarrow \text{Encode JPEG} \longrightarrow \text{Kirim ke Browser}$$
**Dampak Buruk:** Karena inferensi memakan waktu ~300 ms, browser web hanya menerima gambar baru setiap 300 ms. Video di browser terlihat **patah-patah (ngeframe/freeze)**, meskipun kamera fisiknya mampu menangkap 30 FPS.

### 4.2. Arsitektur Baru yang Diterapkan di `app.py`
Sistem dipecah menjadi **4 Thread Asinkron yang Berjalan Bersamaan (Decoupled Pipeline)**:

```
                  ┌──────────────────────────────────────────────┐
                  │          THREAD 1: CAMERA CAPTURE            │
                  │   Picamera2 / CSI Hardware (20 FPS konstan)  │
                  └──────────────────────┬───────────────────────┘
                                         │ Frame Baru (notify_all)
                   ┌─────────────────────┴─────────────────────┐
                   ▼                                           ▼
┌─────────────────────────────────────┐     ┌─────────────────────────────────────┐
│     THREAD 2: STREAM RENDERING      │     │         THREAD 3: INFERENSI         │
│  - Ambil frame kamera terbaru       │     │  - Ambil frame kamera terbaru       │
│  - Gambar box dari cache deteksi    │     │  - Model NCNN Predict (300 ms)      │
│  - Encode ke JPEG (.jpg)            │     │  - Update cache hasil deteksi       │
│  - Laju: 20 FPS (Sangat Mulus!)     │     │  - Evaluasi ROI & Tracking          │
└──────────────────┬──────────────────┘     └──────────────────┬──────────────────┘
                   │                                           │ Objek Keluar ROI
                   ▼                                           ▼
┌─────────────────────────────────────┐     ┌─────────────────────────────────────┐
│       HTTP /video/stream (MJPEG)    │     │      THREAD 4: SQLITE QUEUE         │
│       Browser Client Display        │     │  Menulis ke history.db di background│
└─────────────────────────────────────┘     └─────────────────────────────────────┘
```

1. **Thread 1: Camera Capture (`CameraStream`)**
   * Berkomunikasi langsung dengan sensor OV5647 via driver native `Picamera2`.
   * Menangkap citra secara kontinu pada kecepatan 20 FPS ke dalam variabel memori `latest_raw_frame`.
   * Bersifat *zero-lag bufferless*: frame lama langsung ditimpa frame baru (tidak ada antrean memori menumpuk).
2. **Thread 2: Stream Rendering (`stream_render_loop`)**
   * Mengambil frame terbaru dari Thread 1 segera setelah tersedia.
   * Menempelkan bounding box dari hasil deteksi AI terakhir yang tersimpan di memori cache (`cached_detections`).
   * Melakukan encoding JPEG dan menyediakannya untuk rute `/video/stream`.
   * **Hasil:** Stream di browser berjalan mulus pada **15–20 FPS** tanpa terpengaruh lamanya waktu inferensi model AI.
3. **Thread 3: Inferensi NCNN (`inference_loop`)**
   * Berjalan independen. Ketika model selesai memproses 1 frame, thread ini mengambil frame kamera terbaru yang tersedia saat itu.
   * Frame-frame yang terlewat saat model sedang sibuk otomatis di-*drop* (dilewati). Ini memastikan deteksi selalu terjadi pada kondisi real-time, bukan masa lalu.
   * Menghitung umur hasil deteksi (*detection age*) untuk transparansi data latensi.
4. **Thread 4: Database Asynchronous Writer (`db_worker`)**
   * Operasi penulisan ke kartu microSD sering mengalami *I/O latency spike* (5–50 ms).
   * Data komponen yang terverifikasi dimasukkan ke dalam antrean `db_write_queue`.
   * Thread background mengambil data dari antrean dan menyimpannya ke tabel SQLite tanpa menghentikan thread inferensi maupun video stream.

---

## 5. LOGIKA ROI (REGION OF INTEREST) & DEBOUNCING TRACKING

### 5.1. Zona Inspeksi (ROI)
Pada citra video beresolusi $W \times H$, didefinisikan koordinat normalisasi:
$$\text{ROI} = (x_{\min}=0.25,\; x_{\max}=0.75,\; y_{\min}=0.10,\; y_{\max}=0.90)$$
* **Zona Inspeksi (Tengah):** Hanya area tengah (kotak kuning) tempat komponen elektronika berada dalam fokus tajam dan pencahayaan optimal.
* **Di Luar ROI:** Bounding box komponen digambar tipis berwarna abu-abu dan **tidak dihitung/tidak disimpan ke database**.

### 5.2. Logika Debouncing & Confidence Tertinggi (`process_tracking`)
Saat objek berjalan di atas konveyor melintasi ROI, objek tersebut akan tertangkap kamera dalam beberapa frame berturut-turut.
1. **Pemeriksaan Titik Tengah (Centroid):**
   $$c_x = \frac{x_1 + x_2}{2}, \quad c_y = \frac{y_1 + y_2}{2}$$
   Jika $c_x \in [0.25, 0.75]$ dan $c_y \in [0.10, 0.90]$, objek dianggap berada di dalam Zona Inspeksi.
2. **Tracking State (`active_tracking` Dictionary):**
   * Saat objek pertama kali masuk ROI, namanya dimasukkan ke dictionary dengan atribut:
     `{"last_seen": timestamp_sekarang, "conf": nilai_confidence}`
   * Pada frame-frame berikutnya selama objek masih di ROI:
     * `last_seen` diperbarui ke waktu terkini.
     * Nilai `conf` diperbarui hanya jika confidence pada frame baru **lebih tinggi** daripada nilai sebelumnya:
       $$\text{conf}_{\text{disimpan}} = \max(\text{conf}_{\text{lama}},\; \text{conf}_{\text{baru}})$$
3. **Pencatatan Permanen (Keluaran ROI):**
   * Jika selisih waktu sekarang dengan `last_seen` melebihi ambang batas `EXIT_DELAY` (1.0 detik):
     $$\Delta t = t_{\text{now}} - t_{\text{last\_seen}} > 1.0\text{ detik}$$
   * Objek dinyatakan telah **keluar dari zona inspeksi**.
   * Objek dicatat **tepat 1 kali** ke database SQLite dengan nilai confidence tertinggi yang pernah dicapainya.
   * Objek dihapus dari `active_tracking` agar memori kembali bersih.

---

## 6. MEKANISME STREAMING VIDEO (MJPEG) & TELEMETRI (SSE)

Sistem menggunakan **arsitektur komunikasi ganda** (dual-channel) berbasis HTTP:

### 6.1. Video Stream via Multipart MJPEG (`/video/stream`)
* Format: `multipart/x-mixed-replace; boundary=frame`.
* Cara Kerja: Browser klien membuka satu koneksi HTTP persisten. Server Flask mengirim frame JPEG secara berurutan dengan header HTTP boundary. Browser me-replace citra lama dengan citra baru di elemen `<img>` tanpa memerlukan library JavaScript pemutar video pihak ketiga.
* Efisiensi: Server hanya mengirim frame baru jika `latest_frame_id` bertambah, sehingga tidak ada bandwidth yang terbuang untuk frame duplikat.

### 6.2. Telemetri Real-Time via Server-Sent Events (SSE) (`/results/stream`)
* Format: `text/event-stream`.
* Cara Kerja: Koneksi satu arah (*unidirectional push*) dari server ke browser via protokol standar HTML5 `EventSource`.
* Data Payload (JSON):
  * Daftar komponen yang sedang terdeteksi beserta koordinat box dan confidence.
  * Waktu latensi inferensi (`ms`).
  * Data komponen baru yang lolos inspeksi (`newly_saved`) untuk langsung ditambahkan ke tabel web.
* **Kenapa SSE, bukan WebSocket?**
  * SSE berjalan di atas protokol HTTP standar (port 5000), tanpa perlu handshake protokol ganda seperti WebSocket.
  * Memiliki fitur *auto-reconnect* otomatis jika jaringan WiFi terputus sesaat.
  * Jauh lebih hemat resource CPU dan memori di Raspberry Pi karena sifatnya yang satu arah (server ke klien).

---

## 7. DAFTAR LIBRARY, TEKNOLOGI, & FUNGSINYA

| Nama Library / Tool | Versi / Tipe | Fungsi & Peran Spesifik dalam Proyek |
|---|---|---|
| **Python** | 3.10+ (ARM64) | Bahasa pemrograman utama server backend. |
| **Flask** | 3.x (Web Framework) | Menangani routing endpoint REST API, penyajian halaman dashboard web, MJPEG generator, dan streaming SSE. |
| **Picamera2** | Native RPi Library | Mengakses hardware kamera CSI secara low-level via subsistem `libcamera` Linux, mengatur manual exposure, analogue gain, dan FPS kamera. |
| **OpenCV (`cv2`)** | 4.x / 5.x | Mengolah array citra numpy: menggambar bounding box komponen, teks label, kotak ROI kuning, dan kompresi JPEG (`imencode`). |
| **NumPy** | 2.x | Operasi matriks citra, manipulasi dimensi tensor gambar, dan perhitungan matematika titik tengah centroid. |
| **NCNN** | Tencent Library | Engine inferensi neural network yang mengeksekusi model deteksi pada core CPU Raspberry Pi dengan instruksi ARM NEON. |
| **Ultralytics** | 8.x | Wrapper Python tingkat tinggi untuk memuat struktur model NCNN, melakukan pemrosesan non-maximum suppression (NMS), dan kalkulasi koordinat `xyxyn`. |
| **SQLite3** | Bawaan Python | Database relasional file-tunggal (`history.db`) untuk mencatat timestamp, nama komponen, dan akurasi tanpa beban server database eksternal (seperti MySQL/PostgreSQL). |
| **Vanilla HTML/CSS/JS** | Web Frontend | Antarmuka dashboard responsif industrial (Dark Mode) tanpa framework berat (React/Vue) agar loading di browser instan. |

---

## 8. TANYA JAWAB KRITIS UJIAN SKRIPSI (BANK PERTANYAAN DOSEN PENGUJI)

Berikut adalah daftar pertanyaan mendalam yang sering diajukan oleh dosen penguji beserta jawaban ilmiah dan berbasis teknis:

---

### Q1: "Kenapa Anda tidak memakai framework TensorFlow Lite (TFLite) atau ONNX, melainkan memilih NCNN?"
**Jawaban Teknis:**
> *"TFLite dan ONNX adalah runtime generik lintas platform. Namun, untuk arsitektur prosesor ARM Cortex-A72 pada Raspberry Pi 4, **NCNN** dirancang secara khusus oleh Tencent dengan optimasi assembly instruksi **ARM NEON SIMD**. NCNN memiliki overhead memori paling kecil (tidak memerlukan runtime environment raksasa) dan menghasilkan throughput perkalian matriks yang lebih cepat dan konsisten pada CPU ARM dibanding ONNX Runtime tanpa bantuan hardware akselerator GPU."*

---

### Q2: "Video di browser Anda terlihat lancar (15–20 FPS), apakah itu berarti model AI Anda berhasil berjalan 20 FPS di Raspberry Pi?"
**Jawaban Jujur & Ilmiah:**
> *"Tidak, Pak/Bu. Model YOLOv8n NCNN beresolusi 640x640 pada Raspberry Pi 4 memiliki kecepatan inferensi sekitar **3 hingga 4 FPS** (latensi ~250–320 ms). Tampilan video di browser tetap lancar 15–20 FPS karena saya menerapkan arsitektur **Decoupled Multi-Threading**:*
> 1. *Thread Capture & Stream merender frame kamera secara langsung tanpa menunggu AI selesai.*
> 2. *Thread AI berjalan di jalurnya sendiri dan memperbarui kotak deteksi ke memori cache.*
> 3. *Dengan demikian, kecepatan tampilan video tidak terkunci oleh kecepatan inferensi model. Ini adalah standar yang digunakan dalam sistem computer vision industri agar operator visual tidak mengalami disorientasi akibat video yang patah-patah."*

---

### Q3: "Bagaimana cara sistem Anda mencegah objek yang berjalan di konveyor tercatat berkali-kali ke database?"
**Jawaban Logika Algoritma:**
> *"Sistem menggunakan algoritma **ROI Tracking Debouncing**:*
> 1. *Objek hanya diperhitungkan jika titik tengahnya ($c_x, c_y$) berada di dalam **Zona Inspeksi (ROI)**.*
> 2. *Selama objek berada di ROI, sistem tidak langsung menyimpannya ke database, melainkan menyimpannya di memory state dictionary `active_tracking` dan terus memperbarui nilai confidence-nya ke nilai tertinggi ($conf_{\max}$).*
> 3. *Ketika objek bergerak meninggalkan ROI dan tidak terdeteksi lagi selama toleransi `EXIT_DELAY` (1 detik), sistem menyimpulkan objek telah selesai diinspeksi, lalu melakukan penulisan ke database **tepat satu kali** dengan nilai confidence tertinggi yang pernah diraih objek tersebut."*

---

### Q4: "Komponen elektronika di konveyor Anda bergerak. Bagaimana Anda mengatasi motion blur (gambar buram) pada kamera?"
**Jawaban Hardware & Optik:**
> *"Pada objek bergerak, motion blur terjadi jika sensor kamera menggunakan auto-exposure dengan waktu rana (exposure time) yang terlalu panjang. Di aplikasi ini, saya menyediakan parameter kontrol langsung ke sensor hardware OV5647 via driver `Picamera2`:*
> * *`AeEnable = False` (mematikan auto exposure otomatis).*
> * *`ExposureTime = 8000 µs` (mengunci shutter speed pada 8 milidetik).*
> * *`AnalogueGain = 2.5x` (menaikkan gain sensor agar citra tetap terang).*
> *Waktu rana yang pendek membekukan gerakan (*freeze motion*) komponen di atas konveyor sehingga tepian kaki IC dan gelang warna resistor tetap tajam saat diinferensi oleh YOLOv8."*

---

### Q5: "Kenapa Anda menggunakan SQLite dibanding MySQL atau PostgreSQL?"
**Jawaban Efisiensi Sistem:**
> *"Sistem ini adalah perangkat **Edge Computing mandiri**. Menjalankan server database terpisah seperti MySQL atau PostgreSQL di Raspberry Pi akan memakan service daemon di background, mengonsumsi RAM (~150–200 MB), dan menambah beban CPU. **SQLite3** adalah database *serverless* berbasis file lokal yang sangat ringan (zero-configuration). Untuk mencegah operasi penulisan file microSD mengganggu proses deteksi, saya menambahkan arsitektur antrean `queue.Queue` di mana penulisan disk dilakukan secara asinkron oleh thread terpisah."*

---

### Q6: "Apa fungsi parameter `FrameDurationLimits` pada konfigurasi Picamera2?"
**Jawaban Efisiensi Komputasi:**
> *"Secara default, sensor OV5647 berusaha mengirimkan frame sebanyak mungkin (30+ FPS) ke CPU. Pada Raspberry Pi, memproses 30 frame beresolusi tinggi yang sebagian besar tidak diinferensi hanya akan membuang siklus clock CPU dan menaikkan suhu prosesor. Dengan menyetel `FrameDurationLimits = (50000, 50000)` mikrodetik, kita membatasi laju kamera hardware tepat di **20 FPS**. Ini menghemat daya komputasi hingga 30% sehingga core CPU dapat difokuskan untuk inferensi model NCNN."*

---

### Q7: "Kenapa Anda menggunakan SSE (Server-Sent Events) untuk telemetri, bukan polling HTTP biasa (`fetch` tiap 500 ms)?"
**Jawaban Jaringan & Protokol:**
> *"Jika menggunakan teknik polling (`setInterval` / `fetch` dari browser), browser akan terus-menerus membuat koneksi TCP baru, mengirim HTTP header berulang-ulang, dan membebani server Flask (overhead request). Dengan **Server-Sent Events (SSE)**, browser hanya membuka **satu koneksi persisten**. Server akan langsung menembakkan (push) data JSON ke browser hanya saat ada data deteksi baru. Ini menghemat bandwidth jaringan dan menghasilkan respons antarmuka yang real-time."*

---

Dokumen ini mencakup seluruh aspek teknis sistem ElectroCom61. Pelajari bab 4, 5, dan 8 secara mendalam untuk penguasaan materi di hadapan dewan penguji skripsi.
