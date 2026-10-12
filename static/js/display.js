"use strict";

// ==============================================================================
// DASHBOARD LOGIC (CLIENT-SIDE JAVASCRIPT) - ElectroCom61
// ==============================================================================
// Berfungsi untuk mengelola interaksi antarmuka web:
// 1. Mengirim perintah kontrol ke backend (Mulai, Hentikan, Ubah Sensitivitas).
// 2. Menerima streaming data telemetri real-time via Server-Sent Events (SSE).
// 3. Merender grafik persentase akurasi, jumlah objek, dan log aktivitas deteksi.
// 4. Memuat dan menampilkan riwayat inspeksi dari database SQLite.
// ==============================================================================

/* ─── ELEMEN-ELEMEN DOM (DOCUMENT OBJECT MODEL) ────────────────────────────── */
// Header & Status Sistem
const hdDot      = document.getElementById("hd-dot");       // Lampu indikator status (hijau jika aktif)
const hdLbl      = document.getElementById("hd-lbl");       // Teks label status ("SISTEM DETEKSI BERJALAN" / "STANDBY")
const hdTime     = document.getElementById("hd-time");      // Jam digital waktu lokal
const mjpeg      = document.getElementById("mjpeg");        // Elemen <img> untuk menampilkan video MJPEG
const noSignal   = document.getElementById("no-signal");    // Overlay informasi saat sistem standby / kamera mati

// Statistik Telemetri Bawah Video
const dsMs       = document.getElementById("ds-ms");        // Waktu komputasi inferensi (milidetik)
const dsCount    = document.getElementById("ds-count");     // Jumlah objek terdeteksi saat ini
const dsLast     = document.getElementById("ds-last");      // Waktu terakhir deteksi diperbarui

// Kartu Ringkasan (Summary Cards)
const scTotal    = document.getElementById("sc-total");     // Total komponen yang terlihat
const scKelas    = document.getElementById("sc-kelas");     // Jumlah variasi jenis komponen
const scMs       = document.getElementById("sc-ms");        // Latensi model (ms)

// Daftar Komponen & Log
const clsEmpty   = document.getElementById("cls-empty");    // Teks placeholder saat tidak ada objek
const clsList    = document.getElementById("class-list");   // Kontainer daftar komponen per kelas
const eventLog   = document.getElementById("event-log");    // Kontainer log teks aktivitas deteksi

// Kontrol Tombol & Slider
const btnStart   = document.getElementById("btn-start");    // Tombol "Mulai Deteksi"
const btnStop    = document.getElementById("btn-stop");     // Tombol "Hentikan"
const confSlider = document.getElementById("conf-slider");  // Slider pengatur ambang batas confidence
const confVal    = document.getElementById("conf-val");     // Label angka persentase slider (cth: 50%)

// Pilihan Sumber Kamera
const camSelect  = document.getElementById("cam-select");   // Dropdown pilihan (Kamera 0, Kamera 1, IP Cam)
const camInput   = document.getElementById("cam-input");    // Input teks URL untuk DroidCam / IP Cam

// ------------------------------------------------------------------------------
// VISIBILITAS INPUT URL KAMERA
// ------------------------------------------------------------------------------
// Menampilkan kolom input teks hanya jika pengguna memilih opsi "IP Camera / DroidCam"
function updateCamInputVisibility() {
  if (camSelect.value === "url") {
    camInput.style.display = "block";
  } else {
    camInput.style.display = "none";
  }
}
camSelect.addEventListener("change", updateCamInputVisibility);
updateCamInputVisibility();

/* ─── KONTROL API SISTEM (START, STOP, CONFIG) ────────────────────────────── */

// 1. TOMBOL MULAI DETEKSI (START)
btnStart.addEventListener("click", async () => {
  try {
    // Langkah A: Bersihkan riwayat lama dari database sebelum sesi baru dimulai
    await fetch("/api/history", { method: "DELETE" });
    const hl = document.getElementById("history-list");
    if (hl) hl.innerHTML = '<div style="font-size: 0.75rem; color: var(--dim); text-align: center; padding: 10px;">Belum ada data</div>';
    
    // Langkah B: Dapatkan sumber input kamera terpilih
    let camVal = camSelect.value;
    if (camVal === "url") {
      camVal = camInput.value;
    }
    
    // Langkah C: Kirim request POST ke backend Flask untuk menyalakan kamera
    await fetch("/api/start", { 
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cam_index: camVal })
    });
    
    // Ubah status tombol di tampilan
    btnStart.style.display = "none";
    btnStop.style.display  = "block";
    showToast("▶ Memulai sistem deteksi...");
    
    // Sambungkan stream video MJPEG dengan menambahkan query timestamp agar terhindar dari cache browser
    mjpeg.src = "/video/stream?" + new Date().getTime();
  } catch (err) {
    showToast("❌ Gagal memulai kamera");
  }
});

// 2. TOMBOL HENTIKAN DETEKSI (STOP)
btnStop.addEventListener("click", async () => {
  try {
    // Kirim request POST ke backend Flask untuk mematikan kamera
    await fetch("/api/stop", { method: "POST" });
    
    // Kembalikan tampilan tombol ke kondisi awal
    btnStop.style.display  = "none";
    btnStart.style.display = "block";
    showToast("⏹ Sistem deteksi dihentikan");
    lostSignal(); // Tampilkan overlay standby
    
    // Beri konfirmasi penawaran unduh file laporan Excel / CSV setelah pengujian selesai
    setTimeout(() => {
      const mauUnduh = confirm("Sistem deteksi telah dihentikan.\n\nApakah Anda ingin mengunduh laporan Riwayat Deteksi (Excel/CSV) sekarang?");
      if (mauUnduh) {
        window.location.href = "/api/history/export";
      }
    }, 500);

  } catch (err) {
    showToast("❌ Gagal menghentikan kamera");
  }
});

// 3. SLIDER PENGATUR CONFIDENCE THRESHOLD (SENSITIVITAS)
// Event 'input': Memperbarui angka persentase di UI secara instan saat slider digeser
confSlider.addEventListener("input", () => {
  confVal.textContent = confSlider.value + "%";
});

// Event 'change': Mengirim nilai baru ke server Flask setelah slider dilepas
confSlider.addEventListener("change", async () => {
  try {
    const val = parseInt(confSlider.value) / 100.0; // Ubah nilai 0-100 menjadi desimal 0.0-1.0
    await fetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ conf: val })
    });
    showToast(`⚙️ Sensitivitas diubah menjadi ${confSlider.value}%`);
  } catch (err) {
    showToast("❌ Gagal merubah sensitivitas");
  }
});

/* ─── PENGELOLAAN WARNA KELAS & PROGRESS BAR ──────────────────────────────── */
// Palet warna yang digunakan untuk membedakan kategori kelas komponen
const PALETTE = ["#4CAF50","#007acc","#F39C12","#9C27B0","#F44336","#858585","#009688","#e91e63"];
const colorMap = {};
let   colorIdx = 0;

// Memberikan warna unik yang konsisten untuk setiap nama komponen
function classColor(name) {
  if (!colorMap[name]) { 
    colorMap[name] = PALETTE[colorIdx++ % PALETTE.length]; 
  }
  return colorMap[name];
}

// Menentukan warna progress bar berdasarkan skor confidence (akurasi):
// Hijau jika >= 75%, Biru jika >= 55%, Oranye jika >= 40%, Merah jika di bawah 40%
function fillClass(v) {
  if (v >= 0.75) return "#4CAF50";
  if (v >= 0.55) return "#007acc";
  if (v >= 0.40) return "#F39C12";
  return "#F44336";
}

/* ─── JAM DIGITAL WAKTU LOKAL (HEADER) ────────────────────────────────────── */
function updateClock() {
  const now = new Date();
  hdTime.textContent = now.toLocaleTimeString("id-ID", { hour12: false });
}
updateClock();
setInterval(updateClock, 1000); // Diperbarui setiap 1 detik

/* ─── SISTEM NOTIFIKASI TOAST ─────────────────────────────────────────────── */
function showToast(msg, ms = 2800) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.classList.add("show");
  // Sembunyikan notifikasi setelah beberapa detik
  setTimeout(() => el.classList.remove("show"), ms);
}

/* ─── STATUS SINYAL VIDEO FEED ────────────────────────────────────────────── */
let signalTimer = null;

// Dipanggil saat frame video berhasil dimuat (Sistem Aktif)
function gotSignal() {
  noSignal.style.display = "none";
  hdDot.className        = "hd-dot active";
  hdLbl.textContent      = "SISTEM DETEKSI BERJALAN";
  hdLbl.className        = "hd-lbl active";
  clearTimeout(signalTimer);
  // Jika dalam 3 detik tidak ada frame baru masuk, otomatis set ke status standby
  signalTimer = setTimeout(lostSignal, 3000);
}

// Dipanggil saat video terputus atau dihentikan (Sistem Standby)
function lostSignal() {
  noSignal.style.display = "flex";
  hdDot.className        = "hd-dot";
  hdLbl.textContent      = "SISTEM STANDBY";
  hdLbl.className        = "hd-lbl";
}

mjpeg.addEventListener("load", gotSignal);
mjpeg.addEventListener("error", lostSignal);

/* ─── PENCATATAN LOG EVENT DETEKSI SECARA LIVE ────────────────────────────── */
const MAX_LOG = 5; // Batas maksimal baris log di tampilan layar

function addLog(msg) {
  const now = new Date().toLocaleTimeString("id-ID", { hour12: false });
  const item = document.createElement("div");
  item.className = "log-item";
  item.innerHTML = `<span class="log-time">${now}</span><span class="log-msg">${msg}</span>`;
  eventLog.prepend(item); // Masukkan log baru di urutan paling atas
  
  // Hapus log tertua jika melebihi batas MAX_LOG
  while (eventLog.children.length > MAX_LOG) {
    eventLog.removeChild(eventLog.lastChild);
  }
}

/* ─── MERENDER HASIL DETEKSI KE ANTARMUKA (UI RENDERING) ──────────────────── */
let prevCount = -1; // Variabel untuk mendeteksi perubahan jumlah objek

function renderResults(detections, ms) {
  const total = detections.length;

  // Perbarui nilai pada panel status video
  dsMs.textContent    = ms != null ? ms + " ms" : "— ms";
  dsCount.textContent = total;
  dsLast.textContent  = new Date().toLocaleTimeString("id-ID", { hour12: false });

  // Perbarui nilai pada kartu ringkasan
  scTotal.textContent = total;
  scMs.textContent    = ms != null ? ms : "—";

  // Kelompokkan objek berdasarkan nama kelasnya dan hitung akumulasi confidence
  const grouped = {};
  detections.forEach(d => {
    if (!grouped[d.kelas]) grouped[d.kelas] = { count: 0, confSum: 0 };
    grouped[d.kelas].count++;
    grouped[d.kelas].confSum += d.confidence;
  });

  // Urutkan kelas dari rata-rata akurasi tertinggi ke terendah
  const sorted = Object.entries(grouped).sort(
    ([, a], [, b]) => (b.confSum / b.count) - (a.confSum / a.count)
  );

  scKelas.textContent = sorted.length;

  // Jika tidak ada objek yang terdeteksi
  if (sorted.length === 0) {
    clsEmpty.style.display = "block";
    [...clsList.querySelectorAll(".cls-item")].forEach(el => el.remove());
  } else {
    clsEmpty.style.display = "none";
    [...clsList.querySelectorAll(".cls-item")].forEach(el => el.remove());

    // Buat elemen HTML baris untuk setiap jenis komponen
    sorted.forEach(([kelas, data]) => {
      const avg = data.confSum / data.count;     // Rata-rata confidence
      const pct = (avg * 100).toFixed(1);        // Format persentase (cth: 92.4%)
      const col = classColor(kelas);
      const fc  = fillClass(avg);

      const item = document.createElement("div");
      item.className = "cls-item";
      item.innerHTML = `
        <div class="ci-dot" style="background:${col};box-shadow:0 0 5px ${col}70;"></div>
        <div class="ci-name">${kelas}</div>
        <div class="ci-count">×${data.count}</div>
        <div class="ci-bar-wrap">
          <div class="ci-bar">
            <div class="ci-fill" style="width:${pct}%;background:${fc};"></div>
          </div>
          <span class="ci-pct">${pct}%</span>
        </div>
      `;
      clsList.appendChild(item);
    });
  }

  // Tambahkan catatan ke Log Event jika terjadi perubahan jumlah objek
  if (total !== prevCount) {
    if (total > 0) {
      const names = sorted.map(([k, d]) => `${k}(${d.count})`).join(", ");
      addLog(`${total} objek: ${names}`);
    } else if (prevCount > 0) {
      addLog("Tidak ada objek terdeteksi");
    }
    prevCount = total;
  }
}

/* ─── SERVER-SENT EVENTS (SSE) - SUBSCRIBER TELEMETRI ─────────────────────── */
function connectSSE() {
  // Membuka kanal koneksi streaming event dari server Flask
  const es = new EventSource("/results/stream");

  // Handler saat menerima pesan data telemetri baru dari server
  es.onmessage = e => {
    try {
      const data = JSON.parse(e.data);
      if (data.running) {
        // Tampilkan hasil deteksi dan inferensi waktu (ms)
        renderResults(data.detections, data.ms);
        gotSignal();
        
        // Jika ada barang yang baru saja selesai diinspeksi dan disimpan ke SQLite,
        // segera tambahkan ke tabel daftar riwayat di layar
        if (data.newly_saved && data.newly_saved.length > 0) {
           data.newly_saved.forEach(item => appendHistory(item));
        }
      }
    } catch (_) {}
  };

  // Handler jika koneksi terputus: coba hubungkan ulang secara otomatis setelah 3 detik
  es.onerror = () => {
    es.close();
    setTimeout(connectSSE, 3000);
  };
}

/* ─── PENGELOLAAN TAMPILAN RIWAYAT DATABASE ────────────────────────────────── */
const historyList = document.getElementById("history-list");

// Memuat data riwayat terakhir dari database SQLite saat web pertama kali dibuka
async function loadHistory() {
  try {
    const res = await fetch("/api/history?limit=50");
    const data = await res.json();
    if (data.length === 0) return;
    historyList.innerHTML = "";
    // Tampilkan data riwayat
    data.forEach(item => appendHistory(item, false));
  } catch(e) {}
}

// Menambahkan satu baris data riwayat ke kontainer daftar
function appendHistory(item, prepend = true) {
  if (historyList.innerHTML.includes("Belum ada data")) {
    historyList.innerHTML = "";
  }
  const div = document.createElement("div");
  div.style.cssText = "font-size: 0.75rem; padding: 6px; border-bottom: 1px solid var(--border); display: flex; justify-content: space-between;";
  
  // Format waktu: hanya ambil bagian jam:menit:detik (HH:MM:SS)
  const timeStr = item.waktu.split(" ")[1] || item.waktu; 
  const conf    = item.confidence || 0;
  
  div.innerHTML = `<span style="color:var(--dim)">${timeStr}</span> <strong style="color:var(--text-bright)">${item.kelas}</strong> <span style="color:var(--primary)">${conf}%</span>`;
  
  // Jika prepend=true, tambahkan item di bagian atas (terbaru di atas)
  if (prepend) {
    historyList.prepend(div);
  } else {
    historyList.appendChild(div);
  }
}

// Inisialisasi awal saat script dimuat
connectSSE();       // Sambungkan ke SSE telemetry feed
loadHistory();      // Ambil data riwayat yang tersimpan di database
showToast("🖥️ Display siap — sistem standby");
