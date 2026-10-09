"use strict";

/* ─── DOM ─────────────────────────────────────────────── */
const hdDot     = document.getElementById("hd-dot");
const hdLbl     = document.getElementById("hd-lbl");
const hdTime    = document.getElementById("hd-time");
const mjpeg     = document.getElementById("mjpeg");
const noSignal  = document.getElementById("no-signal");

const dsMs      = document.getElementById("ds-ms");
const dsCount   = document.getElementById("ds-count");
const dsLast    = document.getElementById("ds-last");

const scTotal   = document.getElementById("sc-total");
const scKelas   = document.getElementById("sc-kelas");
const scMs      = document.getElementById("sc-ms");

const clsEmpty  = document.getElementById("cls-empty");
const clsList   = document.getElementById("class-list");
const eventLog  = document.getElementById("event-log");

const btnStart  = document.getElementById("btn-start");
const btnStop   = document.getElementById("btn-stop");
const confSlider= document.getElementById("conf-slider");
const confVal   = document.getElementById("conf-val");

const camSelect = document.getElementById("cam-select");
const camInput  = document.getElementById("cam-input");

function updateCamInputVisibility() {
  if (camSelect.value === "url") {
    camInput.style.display = "block";
  } else {
    camInput.style.display = "none";
  }
}
camSelect.addEventListener("change", updateCamInputVisibility);
updateCamInputVisibility();

/* ─── API CONTROL ─────────────────────────────────────── */
btnStart.addEventListener("click", async () => {
  try {
    // 1. Bersihkan riwayat lama dari database sebelum mulai sesi baru
    await fetch("/api/history", { method: "DELETE" });
    const hl = document.getElementById("history-list");
    if(hl) hl.innerHTML = '<div style="font-size: 0.75rem; color: var(--dim); text-align: center; padding: 10px;">Belum ada data</div>';
    
    // 2. Ambil sumber kamera
    let camVal = camSelect.value;
    if (camVal === "url") {
      camVal = camInput.value;
    }
    
    // 3. Mulai kamera
    await fetch("/api/start", { 
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cam_index: camVal })
    });
    btnStart.style.display = "none";
    btnStop.style.display = "block";
    showToast("▶ Memulai sistem deteksi...");
    
    mjpeg.src = "/video/stream?" + new Date().getTime();
  } catch (err) {
    showToast("❌ Gagal memulai kamera");
  }
});

btnStop.addEventListener("click", async () => {
  try {
    await fetch("/api/stop", { method: "POST" });
    btnStop.style.display = "none";
    btnStart.style.display = "block";
    showToast("⏹ Sistem deteksi dihentikan");
    lostSignal();
    
    // Pengingat unduh file Excel setelah kamera mati
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

confSlider.addEventListener("input", () => {
  confVal.textContent = confSlider.value + "%";
});

confSlider.addEventListener("change", async () => {
  try {
    const val = parseInt(confSlider.value) / 100.0;
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

/* ─── WARNA PER KELAS ─────────────────────────────────── */
const PALETTE = ["#4CAF50","#007acc","#F39C12","#9C27B0","#F44336","#858585","#009688","#e91e63"];
const colorMap = {};
let   colorIdx = 0;
function classColor(name) {
  if (!colorMap[name]) { colorMap[name] = PALETTE[colorIdx++ % PALETTE.length]; }
  return colorMap[name];
}

function fillClass(v) {
  if (v >= 0.75) return "#4CAF50";
  if (v >= 0.55) return "#007acc";
  if (v >= 0.40) return "#F39C12";
  return "#F44336";
}

/* ─── CLOCK ───────────────────────────────────────────── */
function updateClock() {
  const now = new Date();
  hdTime.textContent = now.toLocaleTimeString("id-ID", { hour12: false });
}
updateClock();
setInterval(updateClock, 1000);

/* ─── TOAST ───────────────────────────────────────────── */
function showToast(msg, ms = 2800) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.classList.add("show");
  setTimeout(() => el.classList.remove("show"), ms);
}

/* ─── MJPEG: deteksi sinyal ada/tidak ────────────────── */
let signalTimer = null;

function gotSignal() {
  noSignal.style.display = "none";
  hdDot.className  = "hd-dot active";
  hdLbl.textContent= "SISTEM DETEKSI BERJALAN";
  hdLbl.className  = "hd-lbl active";
  clearTimeout(signalTimer);
  signalTimer = setTimeout(lostSignal, 3000);
}

function lostSignal() {
  noSignal.style.display = "flex";
  hdDot.className  = "hd-dot";
  hdLbl.textContent= "SISTEM STANDBY";
  hdLbl.className  = "hd-lbl";
}

mjpeg.addEventListener("load", gotSignal);
mjpeg.addEventListener("error", lostSignal);

/* ─── LOG EVENT ───────────────────────────────────────── */
const MAX_LOG = 5;

function addLog(msg) {
  const now = new Date().toLocaleTimeString("id-ID", { hour12: false });
  const item = document.createElement("div");
  item.className = "log-item";
  item.innerHTML = `<span class="log-time">${now}</span><span class="log-msg">${msg}</span>`;
  eventLog.prepend(item);
  while (eventLog.children.length > MAX_LOG) {
    eventLog.removeChild(eventLog.lastChild);
  }
}

/* ─── RENDER HASIL DETEKSI ────────────────────────────── */
let prevCount = -1;

function renderResults(detections, ms) {
  const total = detections.length;

  dsMs.textContent    = ms != null ? ms + " ms" : "— ms";
  dsCount.textContent = total;
  dsLast.textContent  = new Date().toLocaleTimeString("id-ID", { hour12: false });

  scTotal.textContent = total;
  scMs.textContent    = ms != null ? ms : "—";

  const grouped = {};
  detections.forEach(d => {
    if (!grouped[d.kelas]) grouped[d.kelas] = { count: 0, confSum: 0 };
    grouped[d.kelas].count++;
    grouped[d.kelas].confSum += d.confidence;
  });

  const sorted = Object.entries(grouped).sort(
    ([, a], [, b]) => (b.confSum / b.count) - (a.confSum / a.count)
  );

  scKelas.textContent = sorted.length;

  if (sorted.length === 0) {
    clsEmpty.style.display = "block";
    [...clsList.querySelectorAll(".cls-item")].forEach(el => el.remove());
  } else {
    clsEmpty.style.display = "none";
    [...clsList.querySelectorAll(".cls-item")].forEach(el => el.remove());

    sorted.forEach(([kelas, data]) => {
      const avg = data.confSum / data.count;
      const pct = (avg * 100).toFixed(1);
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

/* ─── SSE — Subscribe ke hasil deteksi ──────────────── */
function connectSSE() {
  const es = new EventSource("/results/stream");

  es.onmessage = e => {
    try {
      const data = JSON.parse(e.data);
      if (data.running) {
        renderResults(data.detections, data.ms);
        gotSignal();
        
        // Update riwayat jika ada barang baru yang masuk database
        if (data.newly_saved && data.newly_saved.length > 0) {
           data.newly_saved.forEach(item => appendHistory(item));
        }
      }
    } catch (_) {}
  };

  es.onerror = () => {
    es.close();
    setTimeout(connectSSE, 3000);
  };
}

/* ─── RIWAYAT DATABASE ──────────────────────────────── */
const historyList = document.getElementById("history-list");

async function loadHistory() {
  try {
    const res = await fetch("/api/history?limit=50");
    const data = await res.json();
    if (data.length === 0) return;
    historyList.innerHTML = "";
    // render from bottom to top (newest first)
    data.forEach(item => appendHistory(item, false));
  } catch(e) {}
}

function appendHistory(item, prepend = true) {
  if (historyList.innerHTML.includes("Belum ada data")) {
    historyList.innerHTML = "";
  }
  const div = document.createElement("div");
  div.style.cssText = "font-size: 0.75rem; padding: 6px; border-bottom: 1px solid var(--border); display: flex; justify-content: space-between;";
  
  const timeStr = item.waktu.split(" ")[1]; 
  const conf = item.confidence || 0;
  
  div.innerHTML = `<span style="color:var(--dim)">${timeStr}</span> <strong style="color:var(--text-bright)">${item.kelas}</strong> <span style="color:var(--primary)">${conf}%</span>`;
  
  if (prepend) {
    historyList.prepend(div);
  } else {
    historyList.appendChild(div);
  }
}

connectSSE();
loadHistory();
showToast("🖥️ Display siap — sistem standby");
