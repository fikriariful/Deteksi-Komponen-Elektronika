"use strict";

/* ============================================================
   DOM REFERENCES
============================================================ */
const video       = document.getElementById("video");
const canvasCam   = document.getElementById("canvas-cam");
const camOff      = document.getElementById("cam-off");
const scanLine    = document.getElementById("scan-line");

const hudLeft     = document.getElementById("hud-left");
const statusDot   = document.getElementById("status-dot");
const statusLbl   = document.getElementById("status-lbl");

const vFps        = document.getElementById("v-fps");
const vMs         = document.getElementById("v-ms");
const vCount      = document.getElementById("v-count");

const btnStart    = document.getElementById("btn-start");
const btnStop     = document.getElementById("btn-stop");

const confSlider  = document.getElementById("conf-slider");
const confLabel   = document.getElementById("conf-label");

const resultBadge = document.getElementById("result-badge");
const resultEmpty = document.getElementById("result-empty");
const resultList  = document.getElementById("result-list");

const bottomSheet = document.getElementById("bottom-sheet");
const sheetHandle = document.getElementById("sheet-handle");

/* ============================================================
   UTILITAS
============================================================ */
function showToast(msg, ms = 2800) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.classList.add("show");
  setTimeout(() => el.classList.remove("show"), ms);
}

function getConf() {
  return (parseInt(confSlider.value, 10) / 100).toFixed(2);
}

/* ============================================================
   BOTTOM SHEET — drag naik/turun (mobile)
============================================================ */
const sheetStates = ["", "mid", "full"]; // collapsed, setengah, penuh
let sheetIdx = 0;

sheetHandle.addEventListener("click", () => {
  sheetIdx = (sheetIdx + 1) % sheetStates.length;
  bottomSheet.className = "";
  if (sheetStates[sheetIdx]) bottomSheet.classList.add(sheetStates[sheetIdx]);
});

// Swipe touch untuk sheet
let touchStartY = 0;
sheetHandle.addEventListener("touchstart", e => {
  touchStartY = e.touches[0].clientY;
}, { passive: true });

sheetHandle.addEventListener("touchend", e => {
  const dy = touchStartY - e.changedTouches[0].clientY;
  if (Math.abs(dy) < 20) return; // terlalu kecil, abaikan
  if (dy > 0) {
    // geser ke atas → buka lebih
    sheetIdx = Math.min(sheetIdx + 1, sheetStates.length - 1);
  } else {
    // geser ke bawah → tutup lebih
    sheetIdx = Math.max(sheetIdx - 1, 0);
  }
  bottomSheet.className = "";
  if (sheetStates[sheetIdx]) bottomSheet.classList.add(sheetStates[sheetIdx]);
}, { passive: true });

/* ============================================================
   SLIDER CONFIDENCE
============================================================ */
confSlider.addEventListener("input", () => {
  confLabel.textContent = confSlider.value + "%";
});

/* ============================================================
   WARNA PER INDEKS KELAS (konsisten antar frame)
============================================================ */
const CLASS_COLORS = ["#00ff88","#00d4ff","#ff9500","#c77dff","#ff6b6b","#ffd166","#06d6a0","#ef476f"];
const colorMap = {};
let colorIdx = 0;

function classColor(name) {
  if (!colorMap[name]) {
    colorMap[name] = CLASS_COLORS[colorIdx % CLASS_COLORS.length];
    colorIdx++;
  }
  return colorMap[name];
}

function barClass(conf) {
  if (conf >= 0.75) return "rb-green";
  if (conf >= 0.55) return "rb-cyan";
  if (conf >= 0.40) return "rb-orange";
  return "rb-red";
}

/* ============================================================
   RENDER HASIL DETEKSI (dikelompokkan per kelas)
============================================================ */
function renderResults(detections, ms) {
  // Update stats
  vCount.textContent = detections.length;
  vMs.textContent    = ms != null ? ms : "—";
  resultBadge.textContent = detections.length + " objek";

  if (detections.length === 0) {
    resultEmpty.style.display = "block";
    resultList.innerHTML      = "";
    return;
  }

  // Kelompokkan per kelas
  const grouped = {};
  detections.forEach(d => {
    if (!grouped[d.kelas]) grouped[d.kelas] = { count: 0, confSum: 0 };
    grouped[d.kelas].count++;
    grouped[d.kelas].confSum += d.confidence;
  });

  // Urutkan confidence rata-rata tertinggi dulu
  const sorted = Object.entries(grouped).sort(
    ([, a], [, b]) => (b.confSum / b.count) - (a.confSum / a.count)
  );

  resultEmpty.style.display = "none";
  resultList.innerHTML      = "";

  sorted.forEach(([kelas, data]) => {
    const avg = data.confSum / data.count;
    const pct = (avg * 100).toFixed(1);
    const col = classColor(kelas);
    const bc  = barClass(avg);

    const item = document.createElement("div");
    item.className = "result-item";
    item.innerHTML = `
      <div class="ri-dot" style="background:${col};box-shadow:0 0 5px ${col}80;"></div>
      <div class="ri-name">${kelas}</div>
      <div class="ri-count">×${data.count}</div>
      <div class="ri-bar-wrap">
        <div class="ri-bar">
          <div class="ri-bar-fill ${bc}" style="width:${pct}%"></div>
        </div>
        <span class="ri-pct">${pct}%</span>
      </div>
    `;
    resultList.appendChild(item);
  });
}

/* ============================================================
   GAMBAR BOUNDING BOX DI CANVAS
============================================================ */
function drawBoxes(detections) {
  const w = video.videoWidth;
  const h = video.videoHeight;
  if (!w || !h) return;

  canvasCam.width  = w;
  canvasCam.height = h;
  const ctx = canvasCam.getContext("2d");
  ctx.clearRect(0, 0, w, h);

  detections.forEach(d => {
    const color = classColor(d.kelas);
    const [x1n, y1n, x2n, y2n] = d.box;
    const x1 = x1n * w, y1 = y1n * h;
    const x2 = x2n * w, y2 = y2n * h;
    const bw  = x2 - x1, bh = y2 - y1;

    // Kotak utama
    ctx.strokeStyle = color;
    ctx.lineWidth   = Math.max(2, w / 350);
    ctx.shadowColor = color;
    ctx.shadowBlur  = 6;
    ctx.strokeRect(x1, y1, bw, bh);
    ctx.shadowBlur  = 0;

    // Corner accent (seperti crosshair)
    const cs = Math.min(bw, bh) * 0.2;
    ctx.lineWidth = Math.max(3, w / 200);
    const corners = [
      [x1, y1, 1, 1], [x2, y1, -1, 1],
      [x1, y2, 1, -1], [x2, y2, -1, -1]
    ];
    corners.forEach(([cx, cy, dx, dy]) => {
      ctx.beginPath();
      ctx.moveTo(cx + dx * cs, cy);
      ctx.lineTo(cx, cy);
      ctx.lineTo(cx, cy + dy * cs);
      ctx.stroke();
    });

    // Label chip
    const conf  = (d.confidence * 100).toFixed(0);
    const label = `${d.kelas}  ${conf}%`;
    const fsize = Math.max(11, w / 60);
    ctx.font = `bold ${fsize}px 'Segoe UI', sans-serif`;
    const tw = ctx.measureText(label).width;
    const lh = fsize + 7;
    const lx = x1;
    const ly = y1 - lh < 0 ? y1 + lh + 2 : y1;

    // Background label dengan warna kelas
    ctx.fillStyle = color + "cc";
    roundRect(ctx, lx, ly - lh, tw + 12, lh, 4);
    ctx.fill();

    // Teks
    ctx.fillStyle = "#000";
    ctx.fillText(label, lx + 6, ly - 3);
  });
}

// Helper: gambar rectangle dengan sudut rounded
function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + w - r, y);
  ctx.quadraticCurveTo(x + w, y, x + w, y + r);
  ctx.lineTo(x + w, y + h - r);
  ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
  ctx.lineTo(x + r, y + h);
  ctx.quadraticCurveTo(x, y + h, x, y + h - r);
  ctx.lineTo(x, y + r);
  ctx.quadraticCurveTo(x, y, x + r, y);
  ctx.closePath();
}

/* ============================================================
   LOOP DETEKSI REALTIME
============================================================ */
let camStream   = null;
let camRunning  = false;
let camFrameId  = null;

// FPS
let fpsCount    = 0;
let fpsLastTime = 0;

// Offscreen canvas untuk encode JPEG
const offscreen = document.createElement("canvas");

async function detectLoop() {
  if (!camRunning) return;

  if (video.readyState < 2) {
    camFrameId = requestAnimationFrame(detectLoop);
    return;
  }

  // FPS
  const now = performance.now();
  fpsCount++;
  if (now - fpsLastTime >= 1000) {
    vFps.textContent = fpsCount;
    fpsCount    = 0;
    fpsLastTime = now;
  }

  // Encode frame
  offscreen.width  = video.videoWidth;
  offscreen.height = video.videoHeight;
  offscreen.getContext("2d").drawImage(video, 0, 0);

  try {
    const blob = await new Promise(resolve =>
      offscreen.toBlob(resolve, "image/jpeg", 0.78)
    );
    const form = new FormData();
    form.append("frame", blob, "frame.jpg");
    form.append("conf",  getConf());

    const res  = await fetch("/detect", { method: "POST", body: form });
    const data = await res.json();

    if (res.ok) {
      drawBoxes(data.detections);
      renderResults(data.detections, data.ms);
    }
  } catch (_) { /* abaikan error per-frame */ }

  setTimeout(() => {
    camFrameId = requestAnimationFrame(detectLoop);
  }, 150);
}

/* ============================================================
   TOMBOL MULAI INSPEKSI
============================================================ */
btnStart.addEventListener("click", async () => {
  try {
    camStream = await navigator.mediaDevices.getUserMedia({
      video: {
        facingMode: "environment",
        width:  { ideal: 1280 },
        height: { ideal: 720 },
      },
      audio: false,
    });

    video.srcObject = camStream;
    camOff.style.display  = "none";
    scanLine.style.display= "block";
    hudLeft.style.display = "flex";
    btnStart.style.display= "none";
    btnStop.style.display = "flex";

    // Status aktif
    statusDot.className   = "status-dot active";
    statusLbl.textContent = "INSPEKSI AKTIF";
    statusLbl.className   = "status-lbl active";

    camRunning  = true;
    fpsLastTime = performance.now();
    fpsCount    = 0;
    camFrameId  = requestAnimationFrame(detectLoop);

    // Buka sheet setengah otomatis
    sheetIdx = 1;
    bottomSheet.classList.add("mid");

    showToast("✅ Sistem inspeksi aktif");
  } catch (err) {
    showToast("❌ Kamera tidak dapat dibuka: " + err.message, 4000);
  }
});

/* ============================================================
   TOMBOL HENTIKAN
============================================================ */
btnStop.addEventListener("click", () => {
  camRunning = false;
  if (camFrameId) cancelAnimationFrame(camFrameId);
  if (camStream)  camStream.getTracks().forEach(t => t.stop());
  video.srcObject = null;

  camOff.style.display  = "flex";
  scanLine.style.display= "none";
  hudLeft.style.display = "none";
  btnStop.style.display = "none";
  btnStart.style.display= "flex";

  // Status standby
  statusDot.className   = "status-dot standby";
  statusLbl.textContent = "STANDBY";
  statusLbl.className   = "status-lbl";

  // Bersihkan canvas
  const ctx = canvasCam.getContext("2d");
  ctx.clearRect(0, 0, canvasCam.width, canvasCam.height);

  // Reset stats
  vFps.textContent   = "—";
  vMs.textContent    = "—";
  vCount.textContent = "0";
  resultBadge.textContent   = "0 objek";
  resultEmpty.style.display = "block";
  resultList.innerHTML      = "";

  // Tutup sheet
  sheetIdx = 0;
  bottomSheet.className = "";

  showToast("⏹ Sistem dihentikan");
});
