/* Mach3-Kontrol - arayuz mantigi
   ------------------------------------------------------------------
   Guvenlik acisindan kritik davranislar (ozet):
     * Surekli jog SADECE parmak/fare/tus basili oldugu surece surer;
       birakma, sayfa gizlenmesi, pencere odagi kaybi veya kalp atisinin
       kesilmesi hareketi aninda durdurur.
     * Arayuz kilidi acikken jog tuslari disindaki TUM tuslar yutulur.
     * Program baslatmadan once sunucudan guvenlik denetimi alinir ve
       kullanicinin onaylamasi beklenir.
   ------------------------------------------------------------------ */
"use strict";

const $ = (s, k = document) => k.querySelector(s);
const $$ = (s, k = document) => [...k.querySelectorAll(s)];

const S = {
  istemci: localStorage.getItem("m3_istemci") ||
    (() => { const v = "i" + Math.random().toString(36).slice(2, 10);
             localStorage.setItem("m3_istemci", v); return v; })(),
  belirtec: localStorage.getItem("m3_belirtec") || "",
  durum: null, program: null, denetim: null,
  adim: parseFloat(localStorage.getItem("m3_adim") || "1"),
  jogModu: localStorage.getItem("m3_jogmodu") || "adim",
  kilitli: false, jogAktif: null, kalpZaman: null,
  gorunum: null, sonOlay: 0, ayarlar: null,
};

const ADIMLAR = [0.05, 0.1, 0.5, 1, 5, 10, 25, 50, 100];

/* ================================================================== */
/* Sunucu iletisimi                                                    */
/* ================================================================== */
async function api(uc, veri = {}) {
  const govde = Object.assign({ istemci: S.istemci, belirtec: S.belirtec }, veri);
  let y;
  try {
    y = await fetch(uc, { method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(govde) });
  } catch (e) {
    throw new Error("Sunucuya ulasilamadi (" + e.message + ")");
  }
  const d = await y.json();
  if (d && d.hata) {
    const h = new Error(d.hata);
    h.sebepler = d.sebepler || [];
    throw h;
  }
  return d;
}

function bildir(mesaj, tur = "", sebepler = []) {
  const el = document.createElement("div");
  el.className = "bildirim " + tur;
  el.innerHTML = `<div>${kacir(mesaj)}</div>` + (sebepler.length
    ? `<ul>${sebepler.map(s => `<li>${kacir(s)}</li>`).join("")}</ul>` : "");
  $("#bildirimAlan").append(el);
  setTimeout(() => { el.style.opacity = "0";
                     setTimeout(() => el.remove(), 300); },
             sebepler.length ? 7000 : 3200);
}

const kacir = s => String(s == null ? "" : s)
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

async function calis(fn, basari) {
  try { const r = await fn(); if (basari) bildir(basari, "ok"); return r; }
  catch (e) { bildir(e.message, "hata", e.sebepler || []); return null; }
}

/* ================================================================== */
/* Durum akisi (SSE)                                                   */
/* ================================================================== */
function akisBaslat() {
  const kaynak = new EventSource("/olay");
  kaynak.onmessage = (o) => {
    let d; try { d = JSON.parse(o.data); } catch { return; }
    S.durum = d;
    durumCiz(d);
    if (d.olaylar) d.olaylar.forEach(gunlukEkle);
  };
  kaynak.onerror = () => {
    $("#cipBaglanti").textContent = "● akis koptu";
    $("#cipBaglanti").className = "cip kotu";
    kaynak.close();
    setTimeout(akisBaslat, 1500);
  };
}

function durumCiz(d) {
  // ---- baglanti cipi
  const c = $("#cipBaglanti");
  if (d.bagli) {
    c.textContent = "● " + (d.tur === "simulasyon" ? "SIMULASYON" : "Mach3 bagli");
    c.className = "cip " + (d.tur === "simulasyon" ? "uyari" : "ok");
  } else {
    c.textContent = "● baglanti yok";
    c.className = "cip kotu";
  }
  $("#altBaslik").textContent = d.bagli
    ? (d.tur === "simulasyon" ? "sanal tezgah (deneme modu)" : "Mach3 baglantisi etkin")
    : (d.hata || "baglanti yok");

  // ---- kontrol kilidi
  const k = $("#cipKontrol");
  const sahip = d.kilit && d.kilit.sahip;
  k.textContent = !sahip ? "kontrol: serbest"
    : (sahip === S.istemci ? "kontrol: sizde" : "kontrol: baska cihazda");
  k.className = "cip " + (!sahip ? "" : sahip === S.istemci ? "vurgu" : "uyari");

  // ---- DRO
  const kutu = $("#dro");
  const eksenler = ["X", "Y", "Z"];
  if (kutu.children.length !== eksenler.length) {
    kutu.innerHTML = eksenler.map(e => `
      <div class="dro-satir" data-e="${e}">
        <span class="ad">${e}</span>
        <span class="deger" id="dro${e}">–</span>
        <span class="mak" id="mak${e}"></span>
      </div>`).join("");
  }
  eksenler.forEach(e => {
    const v = d.konum[e], m = d.makine[e];
    $("#dro" + e).textContent = v == null ? "–" : v.toFixed(3);
    $("#mak" + e).textContent = m == null ? "" : "mak " + m.toFixed(2);
    // Limit uyarisi ancak limitler DOGRULANDIYSA anlamlidir; aksi halde
    // varsayilan degerler yuzunden surekli yanlis alarm verir.
    const lim = d.guvenlik.limitler_dogrulandi &&
      d.guvenlik.yumusak_limit && d.guvenlik.yumusak_limit[e];
    const yakin = lim && m != null &&
      (m - lim.min < 5 || lim.max - m < 5);
    $(`.dro-satir[data-e="${e}"]`).classList.toggle("limit-yakin", !!yakin);
  });
  $("#droFeed").textContent = d.feed == null ? "–" : Math.round(d.feed);
  $("#droFro").textContent = d.fro == null ? "–" : Math.round(d.fro);
  $("#droDevir").textContent = d.devir == null ? "–" : Math.round(d.devir);
  const hr = $("#hareketRozet");
  hr.textContent = d.hareket ? "HAREKET" : "durgun";
  hr.className = "etiket" + (d.hareket ? " hareket" : "");

  // ---- klavye kilidi
  const kl = d.klavye || {};
  $("#cipKilit").classList.toggle("gizli", !(kl.aktif || S.kilitli));
  $("#cipKilit").className = "cip vurgu" + (kl.aktif || S.kilitli ? "" : " gizli");
  $("#kilitDurum").innerHTML =
    `hook: ${kl.destekleniyor ? (kl.aktif ? "AKTIF" : "kapali")
                              : "bu isletim sisteminde yok"}` +
    ` · engellenen tus: ${kl.engellenen || 0}` +
    (kl.hata ? `<br><span style="color:var(--tehlike)">${kacir(kl.hata)}</span>` : "") +
    (d.odak && d.odak.destekleniyor
      ? `<br>odak bekcisi: ${d.odak.aktif ? "acik" : "kapali"}` +
        ` · mudahale: ${d.odak.tetikleme || 0}` +
        (d.odak.odak ? ` · on pencere: ${kacir(d.odak.odak).slice(0, 40)}` : "")
      : "");

  // ---- serit
  const serit = $("#serit");
  const mesajlar = [];
  let kritik = false;
  if (!d.bagli) {
    // Baglanti hatasi kucuk yazida kaybolmamali: sebebi burada gosterilir.
    mesajlar.push("BAGLANTI YOK" + (d.hata ? " — " + d.hata : "") +
      "   ·   teshis icin: python main.py --tani");
    kritik = true;
  }
  if (S.kilitli || kl.aktif) mesajlar.push("KILIT ACIK — yalnizca jog tuslari gecerli");
  if (d.jog && d.jog.aktif) mesajlar.push(`JOG ${d.jog.eksen}${d.jog.yon > 0 ? "+" : "−"}`);
  if (d.bagli && !d.guvenlik.limitler_dogrulandi)
    mesajlar.push("Yumusak limitler dogrulanmadi — Guvenlik sekmesinden kontrol edin");
  serit.textContent = mesajlar.join("   ·   ");
  serit.className = "serit" + (mesajlar.length ? "" : " gizli") +
    (kritik || S.kilitli || kl.aktif ? " tehlike" : "");

  guvenlikDurumCiz(d);
}

/* ================================================================== */
/* Sekmeler / tema                                                     */
/* ================================================================== */
$$(".sekme").forEach(b => b.onclick = () => {
  $$(".sekme").forEach(x => x.classList.remove("aktif"));
  $$(".sayfa").forEach(x => x.classList.remove("aktif"));
  b.classList.add("aktif");
  $("#sayfa-" + b.dataset.sekme).classList.add("aktif");
  if (b.dataset.sekme === "ayarlar" || b.dataset.sekme === "guvenlik") ayarlariYukle();
});

$("#temaBtn").onclick = () => {
  const s = document.documentElement.dataset.tema === "dark" ? "light" : "dark";
  document.documentElement.dataset.tema = s;
  localStorage.setItem("m3_tema", s);
};
if (localStorage.getItem("m3_tema"))
  document.documentElement.dataset.tema = localStorage.getItem("m3_tema");

/* ================================================================== */
/* Durdurma                                                            */
/* ================================================================== */
async function acilDurdur(sebep = "arayuz") {
  jogBitir();
  try { await api("/api/acil", { sebep }); bildir("ACIL DURDUR gonderildi", "hata"); }
  catch (e) { bildir(e.message, "hata"); }
}
$("#acilBtn").onclick = () => acilDurdur("acil dugmesi");
$("#jogDur").onclick = () => acilDurdur("jog DUR");
$("#feedHoldBtn").onclick = () => calis(() => api("/api/feedhold"), "Feed hold");
$("#devamBtn").onclick = () => calis(() => api("/api/devam"), "Devam");
$("#durdurBtn").onclick = () => calis(() => api("/api/durdur"), "Durduruldu");

$("#cipKontrol").onclick = async () => {
  const d = S.durum && S.durum.kilit;
  const zorla = d && d.sahip && d.sahip !== S.istemci
    ? confirm("Kontrol baska bir cihazda. Zorla almak istiyor musunuz?")
    : false;
  if (d && d.sahip && d.sahip !== S.istemci && !zorla) return;
  await calis(() => api("/api/kontrol/al", { zorla }), "Kontrol sizde");
};

/* ================================================================== */
/* Jog                                                                 */
/* ================================================================== */
function adimlariCiz() {
  $("#adimlar").innerHTML = ADIMLAR.map(a =>
    `<button class="adim${a === S.adim ? " aktif" : ""}" data-a="${a}">${a}</button>`
  ).join("");
  $$("#adimlar .adim").forEach(b => b.onclick = () => {
    S.adim = parseFloat(b.dataset.a);
    localStorage.setItem("m3_adim", S.adim);
    adimlariCiz();
  });
}
adimlariCiz();

$$("#jogModu button").forEach(b => b.onclick = () => {
  $$("#jogModu button").forEach(x => x.classList.remove("aktif"));
  b.classList.add("aktif");
  S.jogModu = b.dataset.mod;
  localStorage.setItem("m3_jogmodu", S.jogModu);
});
$$("#jogModu button").forEach(b =>
  b.classList.toggle("aktif", b.dataset.mod === S.jogModu));

const jogFeed = () => parseFloat($("#jogFeed").value) || 1000;

async function jogBasla(eksen, yon) {
  if (S.jogAktif) return;
  S.jogAktif = { eksen, yon };
  try {
    await api("/api/jog/basla", { eksen, yon, feed: jogFeed() });
    S.kalpZaman = setInterval(() => {
      api("/api/jog/kalp").catch(() => jogBitir());
    }, 150);
  } catch (e) {
    S.jogAktif = null;
    bildir(e.message, "hata", e.sebepler || []);
  }
}

function jogBitir() {
  if (S.kalpZaman) { clearInterval(S.kalpZaman); S.kalpZaman = null; }
  if (!S.jogAktif) return;
  S.jogAktif = null;
  $$(".jog").forEach(b => b.classList.remove("basili"));
  api("/api/jog/bitir").catch(() => {});
}

async function jogAdim(eksen, yon) {
  await calis(() => api("/api/jog/adim",
    { eksen, mesafe: yon * S.adim, feed: jogFeed() }));
}

$$(".jog[data-eksen]").forEach(b => {
  const eksen = b.dataset.eksen, yon = parseInt(b.dataset.yon, 10);
  b.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    b.classList.add("basili");
    if (S.jogModu === "surekli") jogBasla(eksen, yon);
  });
  const birak = () => { b.classList.remove("basili"); jogBitir(); };
  b.addEventListener("pointerup", (e) => {
    e.preventDefault();
    if (S.jogModu === "adim") jogAdim(eksen, yon); else birak();
    b.classList.remove("basili");
  });
  b.addEventListener("pointerleave", birak);
  b.addEventListener("pointercancel", birak);
});

// Guvenlik: sayfa gizlenir / pencere odagi giderse hareket durur.
document.addEventListener("visibilitychange", () => { if (document.hidden) jogBitir(); });
window.addEventListener("blur", jogBitir);
window.addEventListener("pagehide", jogBitir);

$("#guvenliZBtn").onclick = () =>
  calis(() => api("/api/guvenli_z"), "Guvenli yukseklige cikiliyor");

/* ---- hassas hareket ---- */
$$(".olcu-satir").forEach(satir => {
  const eksen = satir.dataset.eksen;
  $$("button[data-yon]", satir).forEach(b => b.onclick = () => {
    const mesafe = parseFloat($(".mesafe", satir).value) || 0;
    if (!mesafe) return bildir("Mesafe girin", "uyari");
    calis(() => api("/api/jog/adim", {
      eksen, mesafe: parseInt(b.dataset.yon, 10) * Math.abs(mesafe),
      feed: jogFeed() }));
  });
});

$("#gitBtn").onclick = () => {
  const hedef = {};
  ["X", "Y", "Z"].forEach(e => {
    const v = $("#git" + e).value;
    if (v !== "") hedef[e] = parseFloat(v);
  });
  if (!Object.keys(hedef).length) return bildir("Hedef girin", "uyari");
  calis(() => api("/api/git", { hedef }), "Konumlaniyor");
};
$("#sifiraGitBtn").onclick = () =>
  calis(() => api("/api/sifira_git", { eksenler: ["X", "Y"] }), "X0 Y0'a gidiliyor");

/* ---- sifirlama ---- */
$$(".sifir-satir").forEach(satir => {
  const eksen = satir.dataset.eksen;
  $$("button[data-ofset]", satir).forEach(b => b.onclick = () =>
    sifirla(eksen, 0));
  $$("button[data-yon]", satir).forEach(b => b.onclick = () => {
    const ofset = Math.abs(parseFloat($(".ofset", satir).value) || 0);
    sifirla(eksen, parseInt(b.dataset.yon, 10) * ofset);
  });
});

async function sifirla(eksen, ofset) {
  const yon = { X: ["solu", "sagi"], Y: ["gerisi", "ilerisi"],
                Z: ["altisi", "ustu"] }[eksen];
  const metin = ofset === 0
    ? `${eksen} sifiri SU ANKI konum olsun mu?`
    : `${eksen} sifiri, su anki konumun ${Math.abs(ofset)} mm ` +
      `${yon[ofset > 0 ? 1 : 0]} olsun mu?\n(DRO ${(-ofset).toFixed(3)} gosterecek)`;
  if (!confirm(metin)) return;
  await calis(() => api("/api/sifirla", { eksen, ofset }),
              `${eksen} sifirlandi`);
}

$("#droYazBtn").onclick = () => calis(() => api("/api/dro", {
  eksen: $("#droEksen").value, deger: parseFloat($("#droDeger").value) || 0
}), "DRO yazildi");

/* ---- MDI ---- */
$("#mdiBtn").onclick = mdiGonder;
$("#mdiSatir").addEventListener("keydown", e => {
  if (e.key === "Enter") { e.stopPropagation(); mdiGonder(); }
});
async function mdiGonder() {
  const satir = $("#mdiSatir").value.trim();
  if (!satir) return;
  const r = await calis(() => api("/api/mdi", { satir }));
  if (r) {
    const el = document.createElement("div");
    el.textContent = "> " + satir;
    $("#mdiGecmis").prepend(el);
    $("#mdiSatir").value = "";
  }
}

/* ================================================================== */
/* Klavye kilidi (tarayici tarafi)                                     */
/* ================================================================== */
const TUS_EKSEN = {
  ArrowLeft:  ["X", -1], ArrowRight: ["X", 1],
  ArrowUp:    ["Y",  1], ArrowDown:  ["Y", -1],
  PageUp:     ["Z",  1], PageDown:   ["Z", -1],
};
const YAZI_ALANI = e =>
  e && (e.tagName === "INPUT" || e.tagName === "TEXTAREA" || e.isContentEditable);

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") { e.preventDefault(); acilDurdur("Esc"); return; }
  if (!S.kilitli) return;
  if (YAZI_ALANI(e.target) && !TUS_EKSEN[e.key]) return;   // ayar yazarken serbest

  const es = TUS_EKSEN[e.key];
  if (es) {
    e.preventDefault();
    if (e.repeat) return;
    if (S.jogModu === "surekli") jogBasla(es[0], es[1]);
    else jogAdim(es[0], es[1]);
    return;
  }
  if (e.key === " ") { e.preventDefault(); api("/api/feedhold").catch(() => {}); return; }
  // Kilit acikken geri kalan HER tus yutulur (Alt, Tab, Win kombinasyonlari...)
  e.preventDefault();
  e.stopPropagation();
}, true);

document.addEventListener("keyup", (e) => {
  if (!S.kilitli) return;
  if (TUS_EKSEN[e.key]) { e.preventDefault(); jogBitir(); }
}, true);

$("#kilitAnahtar").onchange = async (e) => {
  S.kilitli = e.target.checked;
  document.body.classList.toggle("kilitli", S.kilitli);
  const r = await calis(() => api("/api/kilit", { ac: S.kilitli }));
  if (r && S.kilitli && !r.destekleniyor)
    bildir("Windows klavye hooku bu sistemde yok; tarayici kilidi etkin.",
           "uyari");
};

/* ================================================================== */
/* Program & sira                                                      */
/* ================================================================== */
$("#gozatBtn").onclick = () => gezginAc();
$("#gezginKapat").onclick = () => $("#gezginKatman").classList.add("gizli");

async function gezginAc(klasor) {
  const d = await calis(() => api("/api/gozat", { klasor }));
  if (!d) return;
  $("#gezginKatman").classList.remove("gizli");
  $("#gezginYol").textContent = d.klasor;
  const parcalar = [];
  if (d.ust) parcalar.push(
    `<div class="oge" data-k="${kacir(d.ust)}"><span class="ik">↰</span>
     <span class="isim">.. ust klasor</span></div>`);
  parcalar.push(`<div class="oge" data-k="${kacir(d.ev)}"><span class="ik">⌂</span>
     <span class="isim">Ev</span></div>`);
  d.klasorler.forEach(k => parcalar.push(
    `<div class="oge" data-k="${kacir(k.yol)}"><span class="ik">📁</span>
     <span class="isim">${kacir(k.ad)}</span></div>`));
  d.dosyalar.forEach(f => parcalar.push(
    `<div class="oge" data-d="${kacir(f.yol)}"><span class="ik">📄</span>
     <span class="isim">${kacir(f.ad)}</span>
     <span class="sag">${(f.boyut / 1024).toFixed(0)} KB</span></div>`));
  $("#gezgin").innerHTML = parcalar.join("");
  $$("#gezgin .oge").forEach(o => o.onclick = () => {
    if (o.dataset.k) gezginAc(o.dataset.k);
    else { $("#gezginKatman").classList.add("gizli"); programYukle(o.dataset.d); }
  });
}

async function programYukle(yol) {
  const p = await calis(() => api("/api/program/yukle", { yol }),
                        "Program yuklendi");
  if (p) programCiz(p);
}

function programCiz(p) {
  S.program = p;
  $("#dosyaBilgi").innerHTML =
    `<b>${kacir(p.ad)}</b><br>${p.bloklar.length} kesim blogu · birim ${p.birim}` +
    ` · header ${p.header} satir` +
    (p.uyarilar.length ? `<br><span style="color:var(--uyari)">
      ${p.uyarilar.map(kacir).join("<br>")}</span>` : "");

  $("#blokListe").innerHTML = p.bloklar.map(b => `
    <div class="blok" data-i="${b.id}">
      <span class="no">${b.no}</span>
      <span class="bilgi">X${b.x.toFixed(1)} Y${b.y.toFixed(1)} ·
        ${b.kesim_uz.toFixed(0)} mm · ${b.satir} satir</span>
      <span class="sure">${b.kalan_metin || ""}</span>
    </div>`).join("");
  $$("#blokListe .blok").forEach(el => el.onclick = () =>
    blokSec(parseInt(el.dataset.i, 10)));
  blokIsaretle(p.secili_blok);
  sureCiz(p.sure);
  onizlemeCiz();
  $("#denetimKutu").textContent = "Once denetim yapin.";
  $("#onayKutu").checked = false;
  $("#baslatBtn").disabled = true;
}

async function blokSec(i) {
  const p = await calis(() => api("/api/program/blok", { blok: i }));
  if (!p) return;
  S.program = p;
  blokIsaretle(i);
  sureCiz(p.sure);
  onizlemeCiz();
  $("#onayKutu").checked = false;
  $("#baslatBtn").disabled = true;
  $("#denetimKutu").textContent =
    `Blok ${i + 1} secildi. Baslatmadan once denetim yapin.`;
}

function blokIsaretle(i) {
  S.program.secili_blok = i;
  $$("#blokListe .blok").forEach(el => {
    const n = parseInt(el.dataset.i, 10);
    el.classList.toggle("secili", n === i);
    el.classList.toggle("onceki", n < i);
  });
  const el = $(`#blokListe .blok[data-i="${i}"]`);
  if (el) el.scrollIntoView({ block: "nearest" });
}

function sureCiz(t) {
  if (!t) { $("#sureKutu").textContent = "–"; return; }
  $("#sureKutu").innerHTML = `
    <span class="buyuk">${kacir(t.toplam_metin)}</span>
    <div class="kirilim">
      <span>kesim ${(t.kesim_sn / 60).toFixed(1)} dk</span>
      <span>bosta ${(t.hizli_sn / 60).toFixed(1)} dk</span>
      <span>bekleme ${(t.ek_sn / 60).toFixed(1)} dk</span>
      <span>kesim yolu ${(t.kesim_mm / 1000).toFixed(2)} m</span>
      <span>FRO %${Math.round(t.fro)}</span>
    </div>
    <div class="ipucu">Secili bloktan (${t.baslangic_blok + 1}) sona kadar
      ${t.blok_sayisi} blok. Ivme ve kose kaybi hesaba katildi;
      Ayarlar > Sure kalibrasyonu ile ince ayar yapabilirsiniz.</div>`;
}

/* ---- takim yolu onizlemesi ---- */
function onizlemeCiz() {
  const p = S.program;
  const kap = $("#onizleme");
  if (!p || !p.bloklar.length) {
    kap.innerHTML = `<div class="bos-mesaj">Dosya secin, kesim sirasi burada
      gorunur.</div>`;
    return;
  }
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  p.bloklar.forEach(b => {
    x0 = Math.min(x0, b.bbox[0]); y0 = Math.min(y0, b.bbox[1]);
    x1 = Math.max(x1, b.bbox[2]); y1 = Math.max(y1, b.bbox[3]);
  });
  const pay = Math.max((x1 - x0), (y1 - y0)) * 0.06 + 2;
  x0 -= pay; y0 -= pay; x1 += pay; y1 += pay;
  if (!S.gorunum || S.gorunum.ad !== p.ad)
    S.gorunum = { ad: p.ad, x: x0, y: y0, g: x1 - x0, y2: y1 - y0 };

  const secili = p.secili_blok;
  const numara = $("#numaraGoster").checked;
  const tasima = $("#tasimaGoster").checked;

  const yollar = p.bloklar.map(b => {
    const sinif = b.id === secili ? "secili" : (b.id > secili ? "aktif" : "");
    return `<path class="yol-kesim ${sinif}" d="${b.komut}"/>`;
  }).join("");

  let tasimaYolu = "";
  if (tasima) {
    for (let i = 1; i < p.bloklar.length; i++) {
      const o = p.bloklar[i - 1], y = p.bloklar[i];
      const ox = o.son ? o.son[0] : o.x, oy = o.son ? o.son[1] : o.y;
      tasimaYolu += `<path class="yol-tasima" d="M${ox} ${oy} L${y.x} ${y.y}"/>`;
    }
  }

  const g = S.gorunum;
  const nb = () => g.y2 / 34;                  // gorunume gore yazi boyutu
  const noktalar = p.bloklar.map(b =>
    `<circle class="yol-nokta ${b.id === secili ? "secili" : ""}"
       cx="${b.x}" cy="${-b.y}" r="${(nb() / 4).toFixed(2)}"/>`).join("");
  const numaralar = numara ? p.bloklar.map(b =>
    `<text class="yol-no ${b.id === secili ? "secili" : ""}"
       x="${b.x + nb() * 0.4}" y="${-b.y - nb() * 0.4}">${b.no}</text>`
  ).join("") : "";

  kap.innerHTML = `
    <svg viewBox="${g.x} ${-(g.y + g.y2)} ${g.g} ${g.y2}"
         preserveAspectRatio="xMidYMid meet">
      <g transform="scale(1,-1)">${yollar}${tasimaYolu}</g>
      <g id="isaretKat" font-size="${nb().toFixed(2)}">${noktalar}${numaralar}</g>
    </svg>`;

  // tekerlek ile yakinlastir, surukleyerek kaydir
  const svg = kap.querySelector("svg");
  const uygula = () => {
    svg.setAttribute("viewBox", `${g.x} ${-(g.y + g.y2)} ${g.g} ${g.y2}`);
    const kat = svg.querySelector("#isaretKat");
    if (kat) {
      kat.setAttribute("font-size", nb().toFixed(2));
      kat.querySelectorAll("circle").forEach(c =>
        c.setAttribute("r", (nb() / 4).toFixed(2)));
    }
  };
  svg.onwheel = (e) => {
    e.preventDefault();
    const k = e.deltaY > 0 ? 1.12 : 0.89;
    g.x += g.g * (1 - k) / 2; g.y += g.y2 * (1 - k) / 2;
    g.g *= k; g.y2 *= k;
    uygula();
  };
  let surukle = null;
  svg.onpointerdown = (e) => { surukle = { x: e.clientX, y: e.clientY };
                               svg.setPointerCapture(e.pointerId); };
  svg.onpointermove = (e) => {
    if (!surukle) return;
    const o = svg.getBoundingClientRect();
    g.x -= (e.clientX - surukle.x) * g.g / o.width;
    g.y += (e.clientY - surukle.y) * g.y2 / o.height;
    surukle = { x: e.clientX, y: e.clientY };
    uygula();
  };
  svg.onpointerup = svg.onpointerleave = () => { surukle = null; };
  svg.ondblclick = () => { S.gorunum = null; onizlemeCiz(); };
}
$("#numaraGoster").onchange = onizlemeCiz;
$("#tasimaGoster").onchange = onizlemeCiz;

/* ---- denetim + baslatma ---- */
$("#denetleBtn").onclick = async () => {
  const d = await calis(() => api("/api/program/denetle", {}));
  if (!d) return;
  S.denetim = d;
  const p = [];
  p.push(d.gecti
    ? `<div class="ok-satir">✓ Denetim gecti (blok ${d.blok + 1}'den baslar)</div>`
    : `<div class="hata-satir">✕ Denetim BASARISIZ — baslatilamaz</div>`);
  if (d.hatalar.length)
    p.push(`<ul>${d.hatalar.map(h =>
      `<li class="hata-satir">${kacir(h)}</li>`).join("")}</ul>`);
  if (d.uyarilar.length)
    p.push(`<ul>${d.uyarilar.map(u =>
      `<li class="uyari-satir">${kacir(u)}</li>`).join("")}</ul>`);
  const k = d.kapsam;
  p.push(`<div class="ipucu">Kapsam (is koordinati):
    X ${fmt(k.X[0])}…${fmt(k.X[1])} · Y ${fmt(k.Y[0])}…${fmt(k.Y[1])} ·
    Z ${fmt(k.Z[0])}…${fmt(k.Z[1])} mm</div>`);
  if (d.sure) p.push(`<div class="ipucu">Tahmini sure:
    <b>${kacir(d.sure.toplam_metin)}</b></div>`);
  $("#denetimKutu").innerHTML = p.join("");
  $("#onayKutu").checked = false;
  $("#baslatBtn").disabled = true;
};
const fmt = v => v == null ? "–" : v.toFixed(1);

$("#onayKutu").onchange = (e) => {
  $("#baslatBtn").disabled = !(e.target.checked && S.denetim && S.denetim.gecti);
};

$("#baslatBtn").onclick = async () => {
  const blok = S.program ? S.program.secili_blok : 0;
  if (!confirm(`Blok ${blok + 1}'den itibaren KESIM BASLATILACAK.\n` +
               `Takim, is parcasi ve bagliliklar hazir mi?`)) return;
  const r = await calis(() => api("/api/program/baslat", { blok, onay: true }));
  if (r) bildir("Program baslatildi: blok " + (blok + 1), "ok");
};

/* ================================================================== */
/* Ayarlar & guvenlik formlari                                         */
/* ================================================================== */
async function ayarlariYukle() {
  const d = await calis(() => api("/api/ayarlar", {}));
  if (!d) return;
  S.ayarlar = d.ayarlar;
  const a = d.ayarlar;

  // limit tablosu
  const eksenler = ["X", "Y", "Z"];
  $("#limitTablo").innerHTML =
    `<tr><th>Eksen</th><th>min (mm)</th><th>max (mm)</th></tr>` +
    eksenler.map(e => {
      const l = a.guvenlik.yumusak_limit[e] || { min: 0, max: 0 };
      return `<tr><td><b>${e}</b></td>
        <td><input class="alan" type="number" id="lim${e}min" value="${l.min}"></td>
        <td><input class="alan" type="number" id="lim${e}max" value="${l.max}"></td>
      </tr>`;
    }).join("");
  $("#limitDogru").checked = !!a.guvenlik.limitler_dogrulandi;
  $("#limitZorunlu").checked = !!a.guvenlik.yumusak_limit_zorunlu;
  $("#limitPayi").value = a.guvenlik.limit_payi;
  $("#gMaxAdim").value = a.guvenlik.max_jog_adim;
  $("#gMaxFeed").value = a.guvenlik.max_jog_feed;
  $("#gGuvenliZ").value = a.guvenlik.guvenli_z;
  $("#gDerinlik").value = a.guvenlik.max_dalis_derinlik;
  $("#gJogSure").value = a.guvenlik.max_surekli_jog_sn;
  $("#gKalp").value = a.guvenlik.jog_kalp_atisi_ms;
  $("#gGuvenliZZorunlu").checked = !!a.guvenlik.guvenli_z_zorunlu;

  $("#baglantiTur").value = a.baglanti.tur;
  $("#progid").value = a.baglanti.progid;
  $("#yoklamaHz").value = a.baglanti.yoklama_hz;
  $("#mach3Baslat").checked = !!a.baglanti.mach3_baslat;
  $("#hizXY").value = a.makine.hizli_hiz.X;
  $("#hizZ").value = a.makine.hizli_hiz.Z;
  $("#ivmeXY").value = a.makine.ivme.X;
  $("#ivmeZ").value = a.makine.ivme.Z;
  $("#sifirYontem").value = a.makine.sifirlama_yontemi || "dro";
  $("#sureKatsayi").value = a.gcode.sure_katsayi;

  // OEM kod katalogu
  const grup = (baslik, liste, alan) => `
    <div class="genis-kart"><h3>${baslik}</h3></div>` +
    liste.map(k => `
      <div class="oem-satir ${k.guven === "dusuk" ? "dusuk" : ""}">
        <span class="ad">${kacir(k.ad)}<small>${kacir(k.aciklama)} ·
          guven: ${k.guven}</small></span>
        <input class="alan" type="number" data-alan="${alan}"
               data-ad="${kacir(k.ad)}" value="${k.kod}">
      </div>`).join("");
  $("#oemKutu").innerHTML = grup("DRO", d.oem.dro, "dro") +
    grup("Dugme", d.oem.buton, "buton") + grup("LED", d.oem.led, "led");
}

$("#guvenlikKaydet").onclick = () => {
  const lim = {};
  ["X", "Y", "Z"].forEach(e => lim[e] = {
    min: parseFloat($("#lim" + e + "min").value),
    max: parseFloat($("#lim" + e + "max").value) });
  calis(() => api("/api/ayarlar", { yama: { guvenlik: {
    yumusak_limit: lim,
    limitler_dogrulandi: $("#limitDogru").checked,
    yumusak_limit_zorunlu: $("#limitZorunlu").checked,
    limit_payi: parseFloat($("#limitPayi").value) || 0,
    max_jog_adim: parseFloat($("#gMaxAdim").value),
    max_jog_feed: parseFloat($("#gMaxFeed").value),
    guvenli_z: parseFloat($("#gGuvenliZ").value),
    max_dalis_derinlik: parseFloat($("#gDerinlik").value),
    max_surekli_jog_sn: parseFloat($("#gJogSure").value),
    jog_kalp_atisi_ms: parseFloat($("#gKalp").value),
    guvenli_z_zorunlu: $("#gGuvenliZZorunlu").checked,
  } } }), "Guvenlik ayarlari kaydedildi");
};

$("#makineKaydet").onclick = () => calis(() => api("/api/ayarlar", { yama: {
  baglanti: { tur: $("#baglantiTur").value, progid: $("#progid").value,
              yoklama_hz: parseFloat($("#yoklamaHz").value) || 10,
              mach3_baslat: $("#mach3Baslat").checked },
  makine: {
    hizli_hiz: { X: parseFloat($("#hizXY").value),
                 Y: parseFloat($("#hizXY").value),
                 Z: parseFloat($("#hizZ").value) },
    ivme: { X: parseFloat($("#ivmeXY").value),
            Y: parseFloat($("#ivmeXY").value),
            Z: parseFloat($("#ivmeZ").value) },
    sifirlama_yontemi: $("#sifirYontem").value },
  gcode: { sure_katsayi: parseFloat($("#sureKatsayi").value) || 1 },
} }), "Makine ayarlari kaydedildi");

$("#oemKaydet").onclick = () => {
  const yama = { dro: {}, buton: {}, led: {} };
  $$("#oemKutu input").forEach(i =>
    yama[i.dataset.alan][i.dataset.ad] = parseInt(i.value, 10));
  calis(() => api("/api/ayarlar", { yama: { oem_ezme: yama } }),
        "OEM kodlari kaydedildi");
};

$("#baglanBtn").onclick = async () => {
  const r = await calis(() => api("/api/baglan", { tur: $("#baglantiTur").value }));
  if (r) bildir("Baglanti denendi", "ok");
};
$("#kesBtn").onclick = () => calis(() => api("/api/kes"), "Baglanti kesildi");

function guvenlikDurumCiz(d) {
  const g = d.guvenlik;
  const satir = (a, b, s = "") =>
    `<div><span>${a}</span><b class="${s}">${b}</b></div>`;
  $("#guvenlikDurum").innerHTML =
    satir("Yumusak limitler", g.yumusak_limit_zorunlu ? "uygulaniyor" : "KAPALI") +
    satir("Limitler dogrulandi", g.limitler_dogrulandi ? "evet" : "HAYIR") +
    satir("Guvenli Z", g.guvenli_z + " mm") +
    satir("En derin elle inis", g.max_dalis_derinlik + " mm") +
    satir("En buyuk jog adimi", g.max_jog_adim + " mm") +
    satir("Surekli jog siniri", g.max_surekli_jog_sn + " sn") +
    satir("Klavye kilidi", d.klavye.aktif ? "ACIK" : "kapali") +
    satir("Odak bekcisi", d.odak.destekleniyor
      ? (d.odak.aktif ? "acik" : "kapali") : "yalnizca Windows");

  const y = d.yetenek || {};
  $("#yetenekKutu").innerHTML = Object.keys(y).length
    ? Object.entries(y).map(([k, v]) =>
        `<span class="${v ? "var" : "yok"}">${kacir(k)}</span>`).join("")
    : `<span>baglanti yok</span>`;
}

/* ================================================================== */
/* Gunluk                                                              */
/* ================================================================== */
function gunlukEkle(o) {
  const el = document.createElement("div");
  el.className = o.tur;
  const t = new Date(o.t * 1000);
  el.innerHTML = `<span class="zaman">${t.toLocaleTimeString("tr-TR")}</span>
    <span class="tur">${kacir(o.tur)}</span>
    <span>${kacir(o.mesaj)}</span>`;
  $("#gunluk").prepend(el);
  while ($("#gunluk").children.length > 300) $("#gunluk").lastChild.remove();
}

/* ================================================================== */
/* PIN / oturum                                                        */
/* ================================================================== */
async function oturumAc(pin) {
  try {
    const d = await api("/api/oturum", { pin: pin || "" });
    S.belirtec = d.belirtec;
    localStorage.setItem("m3_belirtec", d.belirtec);
    $("#pinKatman").classList.add("gizli");
    return true;
  } catch (e) {
    $("#pinKatman").classList.remove("gizli");
    if (pin) { $("#pinHata").textContent = e.message;
               $("#pinHata").classList.remove("gizli"); }
    return false;
  }
}
$("#pinBtn").onclick = () => oturumAc($("#pinAlan").value.trim());
$("#pinAlan").addEventListener("keydown", e => {
  if (e.key === "Enter") { e.stopPropagation(); $("#pinBtn").click(); }
});

/* ================================================================== */
/* Baslangic                                                           */
/* ================================================================== */
(async function baslat() {
  await oturumAc(localStorage.getItem("m3_pin") || "");
  akisBaslat();
  ayarlariYukle();
  const g = await api("/api/gunluk", { adet: 60 }).catch(() => null);
  if (g) { g.olaylar.forEach(gunlukEkle); S.sonOlay = g.olaylar.length; }
  const p = await api("/api/program/bilgi", {}).catch(() => null);
  if (p && p.bloklar) programCiz(p);
})();
