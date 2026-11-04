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
  kilitli: false,
  gorunum: null, sonOlay: 0, ayarlar: null,
};

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
  if (typeof durumMetni === "function") durumMetni(mesaj, tur, sebepler);
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
  tezgahCiz(d);                 // ana ekran (Mach3)
  if (typeof ekranCiz === "function") ekranCiz(d);
  // ---- baglanti cipi
  const c = $("#cipBaglanti");
  if (d.bagli) {
    c.textContent = "● " + (d.tur === "simulasyon" ? "SİMÜLASYON" : "Mach3 bağlı");
    c.className = "cip " + (d.tur === "simulasyon" ? "uyari" : "ok");
  } else {
    c.textContent = "● bağlantı yok";
    c.className = "cip kotu";
  }
  $("#altBaslik").textContent = d.bagli
    ? (d.tur === "simulasyon" ? "sanal tezgâh (deneme modu)" : "Mach3 bağlantısı etkin")
    : (d.hata || "bağlantı yok");

  // ---- kontrol kilidi
  const k = $("#cipKontrol");
  const sahip = d.kilit && d.kilit.sahip;
  k.textContent = !sahip ? "kontrol: serbest"
    : (sahip === S.istemci ? "kontrol: sizde" : "kontrol: başka cihazda");
  k.className = "cip " + (!sahip ? "" : sahip === S.istemci ? "vurgu" : "uyari");

  // ---- klavye kilidi
  const kl = d.klavye || {};
  $("#cipKilit").className = "cip vurgu" + (kl.aktif || S.kilitli ? "" : " gizli");
  $("#kilitDurum").innerHTML =
    `hook: ${kl.destekleniyor ? (kl.aktif ? "AKTİF" : "kapalı")
                              : "bu işletim sisteminde yok"}` +
    ` · engellenen tuş: ${kl.engellenen || 0}` +
    (kl.hata ? `<br><span style="color:var(--tehlike)">${kacir(kl.hata)}</span>` : "") +
    (d.odak && d.odak.destekleniyor
      ? `<br>odak bekçisi: ${d.odak.aktif ? "açık" : "kapalı"}` +
        ` · müdahale: ${d.odak.tetikleme || 0}` +
        (d.odak.odak ? ` · ön pencere: ${kacir(d.odak.odak).slice(0, 40)}` : "")
      : "");

  // ---- serit
  const serit = $("#serit");
  const mesajlar = [];
  let kritik = false;
  if (!d.bagli) {
    // Baglanti hatasi kucuk yazida kaybolmamali: sebebi burada gosterilir.
    mesajlar.push("BAĞLANTI YOK" + (d.hata ? " — " + d.hata : "") +
      "   ·   teşhis için: python main.py --tani");
    kritik = true;
  }
  if (S.kilitli || kl.aktif) mesajlar.push("KİLİT AÇIK — yalnızca jog tuşları geçerli");
  if (d.bagli && !d.guvenlik.limitler_dogrulandi)
    mesajlar.push("Yumuşak limitler doğrulanmadı — Güvenlik sekmesinden kontrol edin");
  serit.textContent = mesajlar.join("   ·   ");
  serit.className = "serit" + (mesajlar.length ? "" : " gizli") +
    (kritik || S.kilitli || kl.aktif ? " tehlike" : "");

  guvenlikDurumCiz(d);
  kalibrasyonCiz(d.kalibrasyon);
}

/* ================================================================== */
/* Sekmeler / tema                                                     */
/* ================================================================== */
function sekmeGec(ad) {
  $$(".sekme").forEach(x => x.classList.toggle("aktif", x.dataset.sekme === ad));
  $$(".sayfa").forEach(x => x.classList.remove("aktif"));
  const s = $("#sayfa-" + ad);
  if (s) s.classList.add("aktif");
  if (ad === "ayarlar" || ad === "guvenlik") ayarlariYukle();
  if (ad === "ayarlar" && typeof tanilamaYenile === "function") tanilamaYenile();
  if (ad === "rapor") raporCiz();
  if (ad === "kalibrasyon") { kalSecimCiz(); kalTazele(true); }
  // Onizleme gizliyken cizilirse SVG olculeri 0 olur; sekmeye gelince tazele
  if (ad === "tezgah" && typeof gkodCiz === "function") gkodCiz(true);
  if (typeof ozGorunurYenile === "function") ozGorunurYenile();
}
$$(".sekme").forEach(b => b.onclick = () => sekmeGec(b.dataset.sekme));

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
  if (typeof jogBitir === "function") jogBitir();
  try { await api("/api/acil", { sebep }); bildir("ACIL DURDUR gonderildi", "hata"); }
  catch (e) { bildir(e.message, "hata"); }
}
$("#acilBtn").onclick = () => acilDurdur("acil dugmesi");
// Onizleme TAM EKRANDAYKEN ust bar gorunmez: panelin kendi ACIL DURDUR dugmesi
$$("[data-acil]").forEach(b => b.onclick = () => acilDurdur("onizleme paneli"));
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

/* ---- hassas hareket ---- */
$$(".olcu-satir").forEach(satir => {
  const eksen = satir.dataset.eksen;
  $$("button[data-yon]", satir).forEach(b => b.onclick = () => {
    const mesafe = parseFloat($(".mesafe", satir).value) || 0;
    if (!mesafe) return bildir("Mesafe girin", "uyari");
    calis(() => api("/api/jog/adim", {
      eksen, mesafe: parseInt(b.dataset.yon, 10) * Math.abs(mesafe),
      }));
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
/* Klavye kısayolları ve jog tuşları: ekran.js                         */
/* ================================================================== */
const YAZI_ALANI = e =>
  e && (e.tagName === "INPUT" || e.tagName === "TEXTAREA" ||
        e.tagName === "SELECT" || e.isContentEditable);

/* Acik bir pencere (PIN, DRO, referans, gezgin, profil) varken jog tuslari
   calismamali: kullanici orada yazi yaziyordur. */
const katmanAcik = () => $$(".katman").some(k => !k.classList.contains("gizli"));

/* ---- DRO yazma + besleme kisayollari (kilitten BAGIMSIZ) ----
   Mach3'teki gibi Ctrl+X/Y/Z ekseni DRO'ya deger yazmayi acar. */
function droKisayolu(e) {
  const k = (S.ayarlar && S.ayarlar.klavye) || {};
  const ctrlGerekli = k.dro_ctrl_gerekli !== false;
  if (ctrlGerekli && !(e.ctrlKey || e.metaKey)) return null;
  if (!ctrlGerekli && (e.ctrlKey || e.metaKey || e.altKey)) return null;
  const harf = (e.key || "").toLowerCase();
  const harita = k.dro_tuslari || { X: "x", Y: "y", Z: "z" };
  for (const eksen of ["X", "Y", "Z"]) {
    if (String(harita[eksen] || "").toLowerCase() === harf) return eksen;
  }
  return null;
}

function froKisayolu(e) {
  const k = (S.ayarlar && S.ayarlar.klavye) || {};
  if (e.key === (k.fro_azalt_tus || "F10")) return -1;
  if (e.key === (k.fro_artir_tus || "F11")) return 1;
  return 0;
}

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

/* Dosya gezgini
   -------------------------------------------------------------------
   * Sunucu en son BAKILAN klasoru hatirlar; gezgin hep kaldiginiz yerden
     acilir (dosya acmadan cikmis olsaniz bile).
   * Varsayilan siralama "en son degisiklik ustte": yeni is klasoru ilk
     siradadir, tarih klasorleri arasinda gezinmek gerekmez.
   * Suzgec istemcide calisir - her tusa basista sunucuya gitmek, ag
     surucusunde gozle gorulur gecikme demektir. */
const GEZ = { veri: null, suz: "" };

const zamanKisa = (t) => {
  if (!t) return "";
  const dk = (Date.now() / 1000 - t) / 60;
  if (dk < 1) return "az once";
  if (dk < 60) return `${Math.round(dk)} dk once`;
  if (dk < 24 * 60) return `${Math.round(dk / 60)} sa once`;
  if (dk < 7 * 24 * 60) return `${Math.round(dk / 1440)} gun once`;
  return new Date(t * 1000).toLocaleDateString("tr-TR");
};

async function gezginAc(klasor, siralama) {
  const d = await calis(() => api("/api/gozat", { klasor, siralama }));
  if (!d) return;
  GEZ.veri = d;
  $("#gezginKatman").classList.remove("gizli");
  $$("#gezginSira button").forEach(b =>
    b.classList.toggle("aktif", b.dataset.s === d.siralama));
  gezginCiz();
  setTimeout(() => { const s = $("#gezginSuz"); if (s) s.focus(); }, 30);
}

function gezginCiz() {
  const d = GEZ.veri;
  if (!d) return;
  $("#gezginYol").textContent = d.klasor;
  const q = GEZ.suz.trim().toLowerCase();
  const uyar = (o) => !q || o.ad.toLowerCase().includes(q);
  const klasorler = d.klasorler.filter(uyar);
  const dosyalar = d.dosyalar.filter(uyar);

  const p = [];
  if (!q) {
    if (d.ust) p.push(
      `<div class="oge" data-k="${kacir(d.ust)}"><span class="ik">↰</span>
       <span class="isim">.. ust klasor</span></div>`);
    p.push(`<div class="oge" data-k="${kacir(d.ev)}"><span class="ik">⌂</span>
       <span class="isim">Ev</span></div>`);
    if (d.son_klasor && d.son_klasor !== d.klasor) p.push(
      `<div class="oge" data-k="${kacir(d.son_klasor)}"><span class="ik">↩</span>
       <span class="isim">Son calisilan klasor</span>
       <span class="sag">${kacir(d.son_klasor)}</span></div>`);
  }
  klasorler.forEach(k => p.push(
    `<div class="oge" data-k="${kacir(k.yol)}"><span class="ik">📁</span>
     <span class="isim">${kacir(k.ad)}</span>
     <span class="sag zaman">${zamanKisa(k.zaman)}</span></div>`));
  dosyalar.forEach(f => p.push(
    `<div class="oge dosya" data-d="${kacir(f.yol)}"><span class="ik">📄</span>
     <span class="isim">${kacir(f.ad)}</span>
     <span class="sag zaman">${zamanKisa(f.zaman)}</span>
     <span class="sag">${(f.boyut / 1024).toFixed(0)} KB</span></div>`));
  if (!p.length)
    p.push(`<div class="bos-mesaj">Eslesen dosya/klasor yok.</div>`);
  $("#gezgin").innerHTML = p.join("");
  $$("#gezgin .oge").forEach(o => o.onclick = () => {
    if (o.dataset.k) { GEZ.suz = ""; $("#gezginSuz").value = ""; gezginAc(o.dataset.k); }
    else { $("#gezginKatman").classList.add("gizli"); programYukle(o.dataset.d); }
  });
}

$("#gezginSuz").addEventListener("input", (e) => {
  GEZ.suz = e.target.value;
  gezginCiz();
});
$("#gezginSuz").addEventListener("keydown", (e) => {
  e.stopPropagation();                       // jog/kilit yakalayicisina gitmesin
  if (e.key === "Escape") { e.target.value = ""; GEZ.suz = ""; gezginCiz(); }
  if (e.key === "Enter") {
    // Tek eslesme varsa dogrudan ac: klasor agacinda hizli gezinme
    const ilk = $("#gezgin .oge");
    if (ilk) ilk.click();
  }
});
$$("#gezginSira button").forEach(b => b.onclick = () =>
  gezginAc(GEZ.veri && GEZ.veri.klasor, b.dataset.s));

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
  blokListeMiniCiz(p);
  tSureCiz(p.sure);
  ozProgram(p);                       // onizleme.js: tum onizlemeler
  $("#denetimKutu").textContent = "Once denetim yapin.";
  $("#onayKutu").checked = false;
  $("#baslatBtn").disabled = true;
  $("#tBaslat").disabled = true;
}

async function blokSec(i) {
  const p = await calis(() => api("/api/program/blok", { blok: i }));
  if (!p) return;
  S.program = p;
  blokIsaretle(i);
  blokMiniIsaretle(i);
  sureCiz(p.sure);
  tSureCiz(p.sure);
  ozProgram(p);
  $("#onayKutu").checked = false;
  $("#baslatBtn").disabled = true;
  $("#tBaslat").disabled = true;
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

/* Takım yolu önizlemesi, vektör sırası ve kenar boşlukları: onizleme.js */

/* ================================================================== */
/* TEZGAH sayfasi - tek ekran (koordinat + G-Code + sira + sureler)     */
/* ================================================================== */
const EKSEN3 = ["X", "Y", "Z"];

function tezgahCiz(d) {
  /* ---- DRO: Mach3 gibi Zero / DRO / REF ---- */
  const kutu = $("#tDro");
  if (kutu.children.length !== EKSEN3.length) {
    kutu.innerHTML = EKSEN3.map(e => `
      <div class="m3-eksen" data-e="${e}">
        <button class="m3-tus sifir" data-sifir="${e}"
                title="${e} iş sıfırını şu anki konuma koy">Zero<br>${e}</button>
        <div class="m3-eks-ad">${e}</div>
        <div class="m3-dro-kutu" data-dro="${e}" title="Ctrl+${e}: değer yaz">
          <span class="is" id="tIs${e}">–</span>
          <span class="mak"><i>tezgâh</i> <b id="tMak${e}">–</b></span>
        </div>
        <button class="m3-tus ref" data-ref="${e}"
                title="Ref ${e}: Mach3 makine sıfırını bu noktaya koy">REF</button>
      </div>`).join("");
    kutu.addEventListener("click", (ev) => {
      const b = ev.target.closest("[data-sifir],[data-ref],[data-dro]");
      if (!b) return;
      if (b.dataset.sifir)
        calis(() => api("/api/sifirla", { eksen: b.dataset.sifir, ofset: 0 }),
              `${b.dataset.sifir} sıfırlandı`);
      else if (b.dataset.ref) refEksen(b.dataset.ref);
      else droKatmanAc(b.dataset.dro);
    });
  }
  // Ikinci satirda MAKINE ham okumasi degil TEZGAH koordinati gosterilir:
  // limitler buna gore denetlenir. Referans yoksa "referanssiz" gorunur.
  EKSEN3.forEach(e => {
    const v = d.konum[e];
    const t = d.tezgah ? d.tezgah[e] : null;
    $("#tIs" + e).textContent = v == null ? "–" : v.toFixed(3);
    const el = $("#tMak" + e);
    el.textContent = t == null ? "referanssız" : t.toFixed(3);
    el.parentElement.classList.toggle("referanssiz", t == null);
    // Limite yakinlik: yalnizca limitler DOGRULANDIYSA anlamlidir
    const lim = d.guvenlik.limitler_dogrulandi &&
      d.guvenlik.yumusak_limit && d.guvenlik.yumusak_limit[e];
    const yakin = lim && t != null && (t - lim.min < 5 || lim.max - t < 5);
    $(`.m3-eksen[data-e="${e}"]`).classList.toggle("limit-yakin", !!yakin);
  });
  referansCiz(d.referans);

  /* ---- tabana kalan pay ---- */
  const tp = d.taban_payi;
  const te = $("#tTabanDeger"), tk = $("#tTaban");
  if (tp == null) {
    te.textContent = "ölçülmedi";
    tk.className = "t-taban yok";
    tk.title = "Tezgâh tabanı ayarlanmadı: Güvenlik › Tezgâh tabanı";
  } else {
    te.textContent = tp.toFixed(2) + " mm";
    tk.className = "t-taban " + (tp < 2 ? "kotu" : tp < 10 ? "uyari" : "ok");
    tk.title = "Makine Z ile tezgâh tabanı arasındaki mesafe";
  }

  /* ---- besleme / devir ---- */
  $("#tFro").textContent = d.fro == null ? "–" : Math.round(d.fro);
  $("#tFeed").textContent = d.feed == null ? "–" : Math.round(d.feed);
  $("#tDevir").textContent = d.devir == null ? "–" : Math.round(d.devir);
  const h = $("#tHareket");
  // RESET (E-Stop) durumu kritik: bu haldeyken Mach3 DRO yazmayi sessizce
  // yok sayar, yani sifirlama/profil yukleme CALISMAZ. Acikca gosterilir.
  if (d.estop) {
    h.textContent = "RESET";
    h.className = "etiket estop";
    h.title = "Mach3 Reset (E-Stop) durumunda: hareket ve sıfırlama yok. " +
              "Reset düğmesine basın.";
  } else {
    h.textContent = d.hareket ? "HAREKET" : "durgun";
    h.className = "etiket" + (d.hareket ? " hareket" : "");
    h.title = "";
  }

  /* ---- is ilerlemesi ---- */
  const t = d.takip || {};
  const p = t.ilerleme;
  $("#tDolgu").style.width = (p == null ? 0 : p * 100).toFixed(1) + "%";
  $("#tDolgu").className = "dolgu" + (t.duraklatildi ? " duraklatildi" : "");
  $("#tGecen").textContent = t.aktif || t.bitti ? t.gecen_metin : "–";
  $("#tKalan").textContent = t.kalan_metin || "–";
  $("#tTahmini").textContent = t.tahmini_metin || "–";
  $("#tYol").textContent = t.toplam_yol_mm
    ? `${(t.yol_mm / 1000).toFixed(2)} / ${(t.toplam_yol_mm / 1000).toFixed(2)} m`
    : "–";
  $("#tSatir").textContent = d.satir ? Math.round(d.satir) : "–";

  /* ---- profiller ---- */
  if (typeof homeCiz === "function") homeCiz(d.profiller);   // home.js
  /* ---- yeni dosya seridi ---- */
  yeniDosyaCiz(d.izleme);
}

/* REF <eksen>: Mach3'un MAKINE koordinatini bulunulan noktada sifirlar. */
async function refEksen(e) {
  if (!confirm(
    `Mach3'ün ${e} MAKİNE koordinatı bu noktada 0 yapılacak (Ref).\n\n` +
    "• Home switch'i OLMAYAN eksen kımıldamaz.\n" +
    "• Home switch'i TANIMLI eksen, switch'i ARAMAYA GİDER (hareket eder).\n\n" +
    "Tezgâh koordinatınız korunur. Devam edilsin mi?")) return;
  const s = await calis(
    () => api("/api/referans", { islem: "makine_sifirla", eksenler: [e] }),
    `${e} makine sıfırı kuruldu`);
  if (s && s.islemeyen && s.islemeyen.length)
    bildir("Ref işlemedi: " + s.islemeyen.join("; "), "uyari");
}

/* ---- tezgah referansi ----
   Referans switch'i olmayan makinelerde Mach3'un makine koordinati tezgahin
   kosesini gostermez. Referans alinmadan yumusak limitler koruyamaz, bu
   yuzden hareket engellenir ve durum belirgin bir seritle gosterilir. */
let _refImza = "";
function referansCiz(r) {
  const serit = $("#referansSerit");
  if (!r) { serit.classList.add("gizli"); return; }
  const imza = JSON.stringify(r);
  if (imza === _refImza) return;
  _refImza = imza;
  if (r.alindi) {
    serit.classList.remove("gizli");
    serit.className = "referans-serit alindi";
    const z = r.zaman
      ? new Date(r.zaman * 1000).toLocaleTimeString("tr-TR") : "";
    serit.innerHTML =
      `<span class="rs-ik">✓</span>
       <span class="rs-yazi">Tezgah referansi alindi${z ? " · " + z : ""}
         <span class="rs-not">limitler bu noktadan olculuyor</span></span>
       <button class="dugme hayalet kucuk" id="refDuzelt">Duzelt</button>
       <button class="dugme hayalet kucuk" id="refBirak">Referansi dusur</button>`;
    $("#refDuzelt").onclick = () => refKatmanAc();
    $("#refBirak").onclick = () =>
      calis(() => api("/api/referans", { islem: "birak" }),
            "Referans dusuruldu");
    return;
  }
  serit.classList.remove("gizli");
  serit.className = "referans-serit yok";
  serit.innerHTML =
    `<span class="rs-ik">⚠</span>
     <span class="rs-yazi"><b>Tezgah referansi alinmadi</b>
       (${kacir((r.eksik || []).join(", "))}) —
       ${r.zorunlu ? "hareket ENGELLI" : "limitler korumuyor"}.
       <span class="rs-not">Kafanin su anki yerinin tezgah koordinatini
         bildirin (sifir kosesindeyseniz 0).</span></span>
     <button class="dugme kucuk" id="refAl">Referans al</button>`;
  $("#refAl").onclick = () => refKatmanAc();
}

/* ------------------------------------------------------------------ */
/* Tezgah referansi penceresi                                          */
/* ------------------------------------------------------------------ */
/* NEDEN elle deger: kafayi her seferinde sifir kosesine goturmek pratik
   degil. Operator bulundugu yerin tezgah koordinatini yazabilirse
   (orn. karsi ucta X=2099) referans dogru yerden kurulur. Eskiden yalniz
   "burasi 0" denebiliyordu; yanlis noktada alinan referans yumusak
   limitleri sessizce ise yaramaz hale getiriyordu. */
function refKatmanAc() {
  if (!S.durum || !S.durum.bagli) { bildir("Mach3 baglantisi yok", "hata"); return; }
  const eksenler = (S.ayarlar && S.ayarlar.makine && S.ayarlar.makine.eksenler)
    || ["X", "Y", "Z"];
  const lim = ((S.ayarlar || {}).guvenlik || {}).yumusak_limit || {};
  $("#refKatmanSatirlar").innerHTML = eksenler.map((e) => {
    const t = (S.durum.tezgah || {})[e];
    const a = lim[e] || {};
    const ar = (a.min == null && a.max == null) ? ""
      : `<span class="ref-ar">tezgah araligi ${a.min ?? "–"} .. ${a.max ?? "–"}</span>`;
    return `<label class="ref-satir">
      <input type="checkbox" class="ref-sec" data-e="${e}" checked>
      <span class="ref-e">${e}</span>
      <input type="number" class="alan ref-d" data-e="${e}" step="0.001"
             value="${t == null ? 0 : t.toFixed(3)}" inputmode="decimal">
      <span class="ref-simdi">su an: ${t == null ? "referanssiz" : t.toFixed(3)}</span>
      ${ar}</label>`;
  }).join("");
  $("#refKatman").classList.remove("gizli");
  setTimeout(() => { const a = $(".ref-d"); if (a) { a.focus(); a.select(); } }, 30);
}

function refKatmanKapat() { $("#refKatman").classList.add("gizli"); }

async function refKatmanKaydet() {
  const eksenler = [], degerler = {};
  for (const s of $$(".ref-sec")) {
    if (!s.checked) continue;
    const e = s.dataset.e;
    const v = parseFloat($(`.ref-d[data-e="${e}"]`).value);
    if (!isFinite(v)) { bildir(`${e} icin gecerli bir sayi girin`, "hata"); return; }
    eksenler.push(e);
    degerler[e] = v;
  }
  if (!eksenler.length) { bildir("En az bir eksen secin", "hata"); return; }
  const ozet = eksenler.map((e) => `${e}=${degerler[e]}`).join("  ");
  if (!confirm("Kafanin SU ANKI yeri su tezgah koordinatinda mi?\n\n" + ozet +
               "\n\nTum yumusak limitler buna gore olculecek. Yanlis deger " +
               "girmek korumayi anlamsiz kilar.")) return;
  refKatmanKapat();
  await calis(() => api("/api/referans", { islem: "al", eksenler, degerler }),
              "Tezgah referansi alindi: " + ozet);
}

/* Mach3'un MAKINE koordinatini bulunulan noktada sifirlar (Ref).
   Tek yol budur: makine DRO'suna deger yazilamaz, sadece sifirlanabilir. */
async function makineSifirla() {
  if (!confirm(
    "Mach3'un MAKINE koordinati bu noktada 0 yapilacak.\n\n" +
    "• Home switch'i OLMAYAN eksenler kimildamaz.\n" +
    "• Home switch'i TANIMLI eksen, switch'i ARAMAYA GIDER (hareket eder).\n\n" +
    "Tezgah koordinatiniz korunur. Devam edilsin mi?")) return;
  refKatmanKapat();
  const s = await calis(
    () => api("/api/referans", { islem: "makine_sifirla" }),
    "Makine sifiri kuruldu");
  if (s && s.islemeyen && s.islemeyen.length) {
    bildir("Bazi eksenlerde Ref islemedi: " + s.islemeyen.join("; "), "uyari");
  }
}

$("#refKatmanIptal").onclick = refKatmanKapat;
$("#refKatmanTamam").onclick = refKatmanKaydet;
$("#refMakineSifir").onclick = makineSifirla;
$("#refKatman").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); refKatmanKaydet(); }
  if (e.key === "Escape") { e.preventDefault(); refKatmanKapat(); }
  e.stopPropagation();
});

/* ---- yeni dosya bildirimi ---- */
let _yeniImza = "";
function yeniDosyaCiz(iz) {
  const serit = $("#yeniDosyaSerit");
  const bekleyen = (iz && iz.bekleyen) || [];
  const imza = bekleyen.map(b => b.yol).join("|");
  if (imza === _yeniImza) return;
  _yeniImza = imza;
  if (!bekleyen.length) { serit.classList.add("gizli"); return; }
  const b = bekleyen[0];
  const konum = b.goreli && b.goreli !== "." ? b.goreli : "";
  serit.classList.remove("gizli");
  serit.innerHTML = `
    <span class="yd-ik">🆕</span>
    <span class="yd-yazi">Yeni G-Code dosyasi: <b>${kacir(b.ad)}</b>
      ${konum ? `<span class="yd-klasor">${kacir(konum)}</span>` : ""}
      ${bekleyen.length > 1
        ? `<span class="yd-adet">+${bekleyen.length - 1} dosya daha</span>` : ""}
    </span>
    <button class="dugme kucuk" id="ydAc">Ac</button>
    <button class="dugme hayalet kucuk" id="ydAtla">Atla</button>
    <button class="dugme hayalet kucuk" id="ydHepsi">Hepsini atla</button>`;
  $("#ydAc").onclick = async () => {
    const p = await calis(() => api("/api/izleme", { islem: "ac", yol: b.yol }),
                          "Program yuklendi");
    if (p) { programCiz(p); sekmeGec("tezgah"); }
  };
  $("#ydAtla").onclick = () =>
    calis(() => api("/api/izleme", { islem: "yoksay", yol: b.yol }));
  $("#ydHepsi").onclick = () =>
    calis(() => api("/api/izleme", { islem: "yoksay" }));
}

/* ---- kesim sirasi (kompakt) ---- */
function blokListeMiniCiz(p) {
  $("#tDosyaAd").textContent = p ? p.ad : "Dosya yuklenmedi";
  $("#tBlokSayi").textContent = p ? `${p.bloklar.length} blok` : "";
  if (!p) { $("#blokListeMini").innerHTML = ""; return; }
  $("#blokListeMini").innerHTML = p.bloklar.map(b => `
    <div class="blok" data-i="${b.id}">
      <span class="no">${b.no}</span>
      <span class="bilgi">X${b.x.toFixed(0)} Y${b.y.toFixed(0)} ·
        ${b.kesim_uz.toFixed(0)} mm</span>
      <span class="sure">${b.kalan_metin || ""}</span>
    </div>`).join("");
  $$("#blokListeMini .blok").forEach(el => el.onclick = () =>
    blokSec(parseInt(el.dataset.i, 10)));
  blokMiniIsaretle(p.secili_blok);
}

function blokMiniIsaretle(i) {
  $$("#blokListeMini .blok").forEach(el => {
    const n = parseInt(el.dataset.i, 10);
    el.classList.toggle("secili", n === i);
    el.classList.toggle("onceki", n < i);
  });
}

function tSureCiz(t) {
  if (!t) { $("#tSure").textContent = "–"; return; }
  $("#tSure").innerHTML =
    `<b>${kacir(t.toplam_metin)}</b> · kesim ${(t.kesim_sn / 60).toFixed(0)} dk ·
     bosta ${(t.hizli_sn / 60).toFixed(0)} dk ·
     yol ${(t.kesim_mm / 1000).toFixed(1)} m · FRO %${Math.round(t.fro)}`;
}

/* ---- tezgah sayfasi dugmeleri ---- */
$("#tGozat").onclick = () => gezginAc();
$("#tFeedHold").onclick = () => calis(() => api("/api/feedhold"));
$("#tDurdur").onclick = () => calis(() => api("/api/durdur"));
$("#tDenetle").onclick = () => denetleYap(true);
$("#tBaslat").onclick = () => baslatYap(true);
$("#froAzalt").onclick = () => froDegistir(-1);
$("#froArtir").onclick = () => froDegistir(1);
$("#refAcBtn").onclick = () => refKatmanAc();

async function froDegistir(yon) {
  const adim = (S.ayarlar && S.ayarlar.klavye &&
                S.ayarlar.klavye.fro_adim) || 5;
  await calis(() => api("/api/fro", { delta: yon * adim }));
}

/* ================================================================== */
/* DRO yazma penceresi (Ctrl+X / Ctrl+Y / Ctrl+Z)                      */
/* ================================================================== */
let _droEksen = "X";

function droKatmanAc(eksen) {
  if (!S.durum || !S.durum.bagli) {
    bildir("Mach3 baglantisi yok", "hata");
    return;
  }
  _droEksen = eksen.toUpperCase();
  const is = S.durum.konum[_droEksen];
  const mak = S.durum.makine[_droEksen];
  const tez = (S.durum.tezgah || {})[_droEksen];
  $("#droKatmanEksen").textContent = _droEksen;
  $("#droKatmanBilgi").innerHTML =
    `su an: is <b>${is == null ? "–" : is.toFixed(3)}</b> ·
     tezgah <b>${tez == null ? "– (referanssiz)" : tez.toFixed(3)}</b> ·
     makine <b>${mak == null ? "–" : mak.toFixed(3)}</b>`;
  // Referans yoksa tezgah kipi tek anlamli secim: limitler ancak boyle
  // devreye girer. Referans varken varsayilan is koordinatidir.
  const refYok = !((S.durum.referans || {}).alindi);
  $$("input[name=droMod]").forEach(
    (r) => { r.checked = (r.value === (refYok ? "tezgah" : "is")); });
  droModCiz();
  const alan = $("#droKatmanDeger");
  alan.value = "0";
  $("#droKatman").classList.remove("gizli");
  setTimeout(() => { alan.focus(); alan.select(); }, 30);
}

function droModu() {
  const s = $$("input[name=droMod]").find((r) => r.checked);
  return s ? s.value : "is";
}

function droModCiz() {
  const m = droModu();
  $("#droModIsEt").classList.toggle("secili", m === "is");
  $("#droModTezgahEt").classList.toggle("secili", m === "tezgah");
  $("#droKatmanIpucu").innerHTML = m === "tezgah"
    ? `<b>Tezgah:</b> “${_droEksen} ekseninde su an tezgahin bu
       noktasindayim” dersiniz. Yumusak limitler bu andan itibaren buradan
       olculur. Is (parca) sifiriniz degismez.`
    : `<b>Is:</b> parca sifiri. 0 yazmak “burasi sifir” demektir.
       Yumusak limitleri <u>degistirmez</u> — tezgah sinirini ayarlamak
       icin ustten “Tezgah koordinati”ni secin.`;
}

function droKatmanKapat() { $("#droKatman").classList.add("gizli"); }

async function droKatmanYaz() {
  const v = parseFloat($("#droKatmanDeger").value);
  if (!isFinite(v)) { bildir("Gecerli bir sayi girin", "hata"); return; }
  const tezgahKipi = droModu() === "tezgah";
  droKatmanKapat();
  if (tezgahKipi) {
    await calis(() => api("/api/referans", {
      islem: "al", eksenler: [_droEksen], degerler: { [_droEksen]: v },
    }), `Tezgah ${_droEksen} = ${v} olarak referanslandi`);
    return;
  }
  await calis(() => api("/api/dro", { eksen: _droEksen, deger: v }),
              `${_droEksen} DRO = ${v}`);
}

$$("input[name=droMod]").forEach(
  (r) => r.addEventListener("change", droModCiz));
$("#droKatmanIptal").onclick = droKatmanKapat;
$("#droKatmanTamam").onclick = droKatmanYaz;
$("#droKatmanDeger").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); droKatmanYaz(); }
  if (e.key === "Escape") { e.preventDefault(); droKatmanKapat(); }
  e.stopPropagation();          // kilit yakalayicisina gitmesin
});

/* ================================================================== */
/* Kalibrasyon (dijital kumpasla)                                      */
/* ================================================================== */
/* Mach3'un COM arayuzu "Steps per unit" ve "Backlash" degerlerini NE OKUR
   NE YAZAR. Bu yuzden akis su sekildedir:
     sunucu  -> hareketi guvenlik katmanindan gecirip gonderir, olcumu
                toplar, yeni degeri hesaplar
     arayuz  -> operatore SU AN ne yapmasi gerektigini ve sonunda Mach3'un
                hangi kutusuna hangi sayiyi yazacagini soyler
   Panel her SSE karesinde degil, yalnizca asama degisince yeniden cizilir;
   yoksa operator olcumu yazarken alan altindan kayar. */
const KAL = { eksen: "X", tur: "olcek", yon: 1, imza: "", ayarOzet: null,
              kayitImza: "" };

const KAL_TUR_NOT = {
  olcek: `<b>Olcek:</b> eksen tam <i>komut</i> kadar gitti mi? Sapma,
    Mach3'teki <b>Steps per unit</b> degerinin yanlis oldugunu gosterir;
    program yeni degeri hesaplar.`,
  bosluk: `<b>Bosluk (backlash):</b> ayni noktaya iki yonden gelinir. Iki
    okuma arasindaki fark, vida/kayis boslugudur ve Mach3'un
    <b>Config → Backlash</b> penceresine yazilir.`,
  dogrulama: `<b>Dogrulama:</b> olcek olcumunun aynisi, ama hicbir sey
    hesaplanmaz — yeni degeri Mach3'e yazdiktan sonra <i>gercekten duzeldi
    mi</i> diye bakmak icindir.`,
};

function kalSecimCiz() {
  $$("#kalEksen button").forEach(b =>
    b.classList.toggle("aktif", b.dataset.e === KAL.eksen));
  $$("#kalTur button").forEach(b =>
    b.classList.toggle("aktif", b.dataset.t === KAL.tur));
  $$("#kalYon button").forEach(b =>
    b.classList.toggle("aktif", parseInt(b.dataset.y, 10) === KAL.yon));
  $("#kalTurNot").innerHTML = KAL_TUR_NOT[KAL.tur] || "";
}
$$("#kalEksen button").forEach(b => b.onclick = () => {
  KAL.eksen = b.dataset.e; kalSecimCiz(); kalAdimBasiCiz();
});
$$("#kalTur button").forEach(b => b.onclick = () => {
  KAL.tur = b.dataset.t; kalSecimCiz();
});
$$("#kalYon button").forEach(b => b.onclick = () => {
  KAL.yon = parseInt(b.dataset.y, 10); kalSecimCiz();
});

/* Form yalnizca sekmeye girerken / kayittan sonra doldurulur; her SSE
   karesinde doldursa operator sayi yazarken alan altindan kayardi. */
function kalFormDoldur() {
  const k = (S.kal && S.kal.ayar) || {};
  if (k.olcum_mesafe != null) $("#kalMesafe").value = k.olcum_mesafe;
  if (k.olcum_feed != null) $("#kalFeed").value = k.olcum_feed;
  if (k.on_yaklasma != null) $("#kalYaklasma").value = k.on_yaklasma;
  if (k.tekrar != null) $("#kalTekrar").value = k.tekrar;
  if (k.kumpas_cozunurluk != null)
    $("#kalCozunurluk").value = k.kumpas_cozunurluk;
  kalAdimBasiCiz();
}

function kalAdimBasiCiz() {
  const k = (S.kal && S.kal.ayar) || {};
  const ab = k.adim_basi || {}, bo = k.bosluk || {};
  $("#kalAdimBasiSatirlar").innerHTML = EKSEN3.map(e => `
    <div class="kal-ab-satir${e === KAL.eksen ? " secili" : ""}">
      <span class="eksen-adi">${e}</span>
      <label>Steps per</label>
      <input type="number" class="alan dar" id="kalAB${e}" step="0.0001"
             value="${ab[e] == null ? "" : ab[e]}" placeholder="Mach3'ten">
      <label>Bosluk</label>
      <input type="number" class="alan dar" id="kalBO${e}" step="0.001"
             value="${bo[e] == null ? "" : bo[e]}" placeholder="mm">
    </div>`).join("");
}

/* ---- ana cizim ----
   Durum akisinda yalnizca KUCUK bir ozet gelir (sunucudaki
   `kalibrasyon_ozet`). Ozet degistiginde sihirbaz aninda yeniden cizilir,
   agir kisim (olcum sonucu, Mach3 talimati, gecmis) ayrica istenir. */
function kalibrasyonCiz(ozet) {
  if (!ozet) return;
  const imza = JSON.stringify(ozet);
  if (imza === KAL.imza) return;
  const ayarDegisti =
    JSON.stringify(ozet.ayar_ozet) !== JSON.stringify(KAL.ayarOzet);
  KAL.imza = imza;
  KAL.ayarOzet = ozet.ayar_ozet;
  kalAkisCiz(ozet.oturum);
  kalTazele(false, ayarDegisti);
}

/* Tam durumu sunucudan alir (sonuc + Mach3 talimati + kayitli degerler). */
async function kalTazele(formDoldur = false, adimBasiTazele = false) {
  const k = await api("/api/kalibrasyon", { islem: "durum" }).catch(() => null);
  if (!k) return;
  S.kal = k;
  const s = k.son_sonuc || (k.oturum && k.oturum.sonuc) || null;
  kalSonucCiz(s);
  kalTalimatCiz(s, k);
  kalKayitliCiz(k);
  if (formDoldur) kalFormDoldur();
  // Adim sayisi alanlarini gereksiz yere yenilemeyiz: operator o sirada
  // Mach3'ten okudugu degeri yaziyor olabilir.
  else if (adimBasiTazele) kalAdimBasiCiz();
}

function kalAkisCiz(o) {
  const kap = $("#kalAkis");
  if (!o) { kap.classList.add("gizli"); kap.innerHTML = ""; return; }
  kap.classList.remove("gizli");
  const olcumler = (o.olcumler || []).map((v, i) =>
    `<span class="kal-olcum">${i + 1}. ${v.toFixed(3)} mm</span>`).join("");
  const hata = o.hata
    ? `<div class="kal-hata">⚠ ${kacir(o.hata)} — hareket yarida kalmis
       olabilir; boslugu bastan alin.</div>` : "";

  let govde = "";
  if (o.asama === "hazir") {
    govde = `<button class="dugme genis" data-kadim="bosluk_al">
      1) Boslugu al ve baslangica gel</button>`;
  } else if (o.asama === "hareket" || o.mesgul) {
    govde = `<div class="kal-bekle">⟳ eksen hareket ediyor…</div>`;
  } else if (o.asama === "okuma_a") {
    govde = `<button class="dugme genis" data-kadim="olc">
      2) Olcum hareketini yap (${o.tur === "bosluk"
        ? "ileri-geri" : (o.yon > 0 ? "+" : "−") + o.komut_mm + " mm"})
      </button>`;
  } else if (o.asama === "okuma_b") {
    govde = `
      <div class="kal-okuma">
        <label>A okumasi</label>
        <input type="number" id="kalA" class="alan dar" step="0.001" value="0">
        <label>B okumasi</label>
        <input type="number" id="kalB" class="alan dar" step="0.001"
               inputmode="decimal" autofocus>
        <button class="dugme" id="kalOlcumKaydet">Olcumu kaydet</button>
      </div>
      <p class="ipucu">Kumpasi sifirladiysaniz A=0 birakin, yalnizca B'yi
        girin. Beklenen deger yaklasik
        <b>${o.tur === "bosluk" ? "0" : o.komut_mm} mm</b>.</p>`;
  } else if (o.asama === "tekrar") {
    govde = `
      <button class="dugme genis" data-kadim="geri">
        Basa don ve tekrarla</button>
      <button class="dugme hayalet" id="kalBitir">Bitir ve sonuca gec</button>`;
  } else if (o.asama === "bitti") {
    govde = `<div class="kal-bitti">✓ Olcum tamamlandi — sagdaki degerleri
      Mach3'e girin.</div>`;
  }

  kap.innerHTML = `
    <div class="kal-asama">
      <span class="kal-rozet">${kacir(o.eksen)} · ${kacir(o.tur)}</span>
      <span class="kal-sayac">${o.alinan}/${o.tekrar} olcum</span>
    </div>
    <div class="kal-yonerge">${kacir(o.yonerge)}</div>
    ${hata}
    <div class="kal-dugmeler">${govde}</div>
    ${olcumler ? `<div class="kal-olcumler">${olcumler}</div>` : ""}`;

  $$("#kalAkis [data-kadim]").forEach(b => b.onclick = () =>
    calis(() => api("/api/kalibrasyon",
                    { islem: "adim", adim: b.dataset.kadim })));
  const kb = $("#kalBitir");
  if (kb) kb.onclick = () =>
    calis(() => api("/api/kalibrasyon", { islem: "bitir" }));
  const ok = $("#kalOlcumKaydet");
  if (ok) {
    ok.onclick = kalOlcumGonder;
    $("#kalB").addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); kalOlcumGonder(); }
      e.stopPropagation();
    });
    setTimeout(() => { const el = $("#kalB"); if (el) el.focus(); }, 30);
  }
}

async function kalOlcumGonder() {
  const a = parseFloat($("#kalA").value);
  const b = parseFloat($("#kalB").value);
  if (!isFinite(b)) { bildir("B okumasini girin", "hata"); return; }
  await calis(() => api("/api/kalibrasyon", {
    islem: "olcum", a: isFinite(a) ? a : 0, b }));
}

function kalSonucCiz(s) {
  const el = $("#kalSonuc");
  if (!s) { el.textContent = "Henuz olcum yapilmadi."; return; }
  const sat = (a, b, sinif = "") =>
    `<div class="ro-satir"><span>${a}</span><b class="${sinif}">${b}</b></div>`;
  let ic = `<div class="ro-baslik">${kacir(s.eksen)} ekseni ·
    ${kacir(s.tur)}</div><div class="ro-izgara">`;
  if (s.tur === "bosluk") {
    ic += sat("Olculen bosluk", s.bosluk_mm.toFixed(4) + " mm",
              s.bosluk_mm > 0.1 ? "kotu" : "");
  } else {
    ic += sat("Komut", s.komut_mm + " mm") +
      sat("Olculen (ort.)", s.olculen_ort.toFixed(4) + " mm") +
      sat("Sapma", (s.hata_mm > 0 ? "+" : "") + s.hata_mm.toFixed(4) + " mm",
          s.gecti ? "iyi" : "kotu") +
      sat("Metrede sapma", (s.hata_m_basina_mm > 0 ? "+" : "") +
          s.hata_m_basina_mm.toFixed(2) + " mm/m") +
      sat("Duzeltme carpani", s.carpan.toFixed(6)) +
      sat("Tolerans", "±" + s.tolerans_mm.toFixed(3) + " mm");
    if (s.tur === "olcek") {
      ic += sat("Mevcut adim/mm", s.eski_adim_basi == null
                ? "girilmedi" : s.eski_adim_basi) +
        sat("YENI adim/mm", s.yeni_adim_basi == null
            ? "hesaplanamadi" : s.yeni_adim_basi, "vurgu-yazi");
    }
  }
  ic += sat("Olcum sayisi", s.olcum_sayisi) +
    sat("Yayilim", s.yayilim_mm.toFixed(4) + " mm",
        s.guvenilir ? "" : "kotu") + `</div>`;
  ic += `<div class="ipucu">Olcumler: ${s.olcumler
    .map(v => v.toFixed(3)).join(" · ")} mm</div>`;
  if ((s.uyarilar || []).length)
    ic += `<ul class="kal-uyari">${s.uyarilar
      .map(u => `<li>${kacir(u)}</li>`).join("")}</ul>`;
  el.innerHTML = ic;
}

function kalTalimatCiz(s, k) {
  const el = $("#kalTalimat");
  const uyg = $("#kalUygula"), elle = $("#kalElleSatir");
  if (!s || !(s.talimat || []).length) {
    el.innerHTML = `Olcum bitince Mach3'te acilacak pencere, kutu adi ve
      yazilacak sayi burada gorunur.`;
    uyg.classList.add("gizli");
    elle.classList.add("gizli");
    return;
  }
  el.innerHTML = s.talimat.map(t => `
    <div class="kal-t">
      <div class="kal-t-bas">${kacir(t.pencere)}
        <span class="kal-t-yer">${kacir(t.yer)}</span></div>
      ${t.alanlar.map(a => `
        <div class="kal-t-alan">
          <span class="kal-t-ad">${kacir(a.ad)}</span>
          ${a.eski == null ? "" : `<span class="kal-t-eski">${a.eski}</span>
            <span class="kal-t-ok">→</span>`}
          <span class="kal-t-yeni">${kacir(a.yeni)}</span>
        </div>`).join("")}
      <ul class="kal-t-not">${t.notlar
        .map(n => `<li>${kacir(n)}</li>`).join("")}</ul>
    </div>`).join("");

  const elleGerek = s.tur === "olcek" && s.yeni_adim_basi == null;
  uyg.classList.toggle("gizli", s.tur === "dogrulama");
  elle.classList.toggle("gizli", !elleGerek);
  if (elleGerek && k && k.ayar) {
    const mevcut = (k.ayar.adim_basi || {})[s.eksen];
    if (mevcut != null && !$("#kalElleDeger").value)
      $("#kalElleDeger").value = (mevcut * s.carpan).toFixed(4);
  }
}

$("#kalKopyala").onclick = async () => {
  const k = S.kal;
  const s = (k && (k.son_sonuc || (k.oturum && k.oturum.sonuc))) || null;
  const metin = s && s.talimat_metni;
  if (!metin) { bildir("Kopyalanacak talimat yok", "uyari"); return; }
  try {
    await navigator.clipboard.writeText(metin);
    bildir("Talimat panoya kopyalandi", "ok");
  } catch {
    // HTTPS olmayan adreste clipboard API kapali olabilir
    const a = document.createElement("textarea");
    a.value = metin; document.body.append(a); a.select();
    try { document.execCommand("copy"); bildir("Kopyalandi", "ok"); }
    catch { bildir("Kopyalanamadi; metni elle secin", "uyari"); }
    a.remove();
  }
};

function kalKayitliCiz(k) {
  const ab = (k.ayar && k.ayar.adim_basi) || {};
  const bo = (k.ayar && k.ayar.bosluk) || {};
  const imza = JSON.stringify([ab, bo, (k.gecmis || []).length]);
  if (imza === KAL.kayitImza) return;
  KAL.kayitImza = imza;
  $("#kalKayitli").innerHTML =
    `<tr><th>Eksen</th><th>Steps per (adim/mm)</th><th>Bosluk (mm)</th></tr>` +
    EKSEN3.map(e => `<tr><td><b>${e}</b></td>
      <td>${ab[e] == null ? "–" : ab[e]}</td>
      <td>${bo[e] == null ? "–" : bo[e]}</td></tr>`).join("");
  const g = (k.gecmis || []).slice().reverse();
  $("#kalGecmis").innerHTML = g.length ? g.map(x => `
    <div class="kal-g">
      <span>${new Date(x.zaman * 1000).toLocaleString("tr-TR")}</span>
      <span class="tur">${kacir(x.eksen)} ${kacir(x.tur)}</span>
      <span>${x.eski == null ? "" : x.eski + " → "}${
        x.yeni == null ? "–" : x.yeni}</span>
      <span>${x.hata_mm == null ? "" :
        (x.hata_mm > 0 ? "+" : "") + x.hata_mm + " mm"}</span>
    </div>`).join("") : `<span class="ipucu-kucuk">Henuz uygulanmis
      kalibrasyon yok.</span>`;
}

/* ---- dugmeler ---- */
$("#kalBasla").onclick = async () => {
  const r = await calis(() => api("/api/kalibrasyon", {
    islem: "basla", eksen: KAL.eksen, tur: KAL.tur, yon: KAL.yon,
    mesafe: parseFloat($("#kalMesafe").value),
    feed: parseFloat($("#kalFeed").value),
    on_yaklasma: parseFloat($("#kalYaklasma").value),
    tekrar: parseInt($("#kalTekrar").value, 10),
  }), `Kalibrasyon oturumu acildi: ${KAL.eksen} / ${KAL.tur}`);
  if (r) KAL.imza = "";
};

$("#kalIptal").onclick = async () => {
  await calis(() => api("/api/kalibrasyon", { islem: "iptal" }),
              "Oturum kapatildi");
  KAL.imza = "";
};

$("#kalAyarKaydet").onclick = async () => {
  const sayi = (id) => {
    const v = $(id).value.trim();
    return v === "" ? null : parseFloat(v);
  };
  const adim_basi = {}, bosluk = {};
  EKSEN3.forEach(e => { adim_basi[e] = sayi("#kalAB" + e);
                        bosluk[e] = sayi("#kalBO" + e); });
  await calis(() => api("/api/kalibrasyon", { islem: "ayar", yama: {
    adim_basi, bosluk,
    olcum_mesafe: parseFloat($("#kalMesafe").value),
    olcum_feed: parseFloat($("#kalFeed").value),
    on_yaklasma: parseFloat($("#kalYaklasma").value),
    tekrar: parseInt($("#kalTekrar").value, 10),
    kumpas_cozunurluk: parseFloat($("#kalCozunurluk").value),
  } }), "Kalibrasyon ayarlari kaydedildi");
  KAL.kayitImza = "";
};

$("#kalUygula").onclick = async () => {
  const k = S.kal;
  const s = (k && (k.son_sonuc || (k.oturum && k.oturum.sonuc))) || null;
  if (!s) { bildir("Uygulanacak sonuc yok", "uyari"); return; }
  const elle = parseFloat($("#kalElleDeger").value);
  const deger = isFinite(elle) ? elle : null;
  if (!confirm("Degeri Mach3'e GERCEKTEN yazdiniz mi?\n\n" +
               "• Motor Tuning'de <SAVE AXIS SETTINGS> dugmesine bastiniz mi?\n" +
               "• Bu kayit yalnizca gecmis icindir; makineyi Mach3'e " +
               "yazdiginiz deger kalibre eder.")) return;
  const r = await calis(() => api("/api/kalibrasyon",
                                  { islem: "uygula", deger }),
                        "Kalibrasyon kaydedildi");
  if (r) {
    KAL.imza = ""; KAL.kayitImza = "";
    $("#kalElleDeger").value = "";
    if ((r.uyarilar || []).length) bildir("Dikkat", "uyari", r.uyarilar);
  }
};

/* ================================================================== */
/* Is raporu                                                           */
/* ================================================================== */
async function raporCiz() {
  const d = await calis(() => api("/api/rapor", { son: true }));
  if (!d || !d.rapor) {
    $("#raporOzet").textContent = "Henuz calistirilmis bir is yok.";
    $("#raporBloklar").innerHTML = "";
    $("#raporOlaylar").innerHTML = "";
    return;
  }
  const r = d.rapor;
  const zaman = (t) => t ? new Date(t * 1000).toLocaleString("tr-TR") : "–";
  const cift = (e, v) => `<div class="ro-satir"><span>${e}</span><b>${v}</b></div>`;
  $("#raporOzet").innerHTML =
    `<div class="ro-baslik">${kacir(r.ad || "–")}</div>` +
    `<div class="ro-yol">${kacir(r.dosya || "")}</div>` +
    `<div class="ro-izgara">` +
    cift("Baslangic", zaman(r.baslangic_t)) +
    cift("Bitis", zaman(r.bitis_t)) +
    cift("Sonuc", kacir(r.bitis_sebep || (r.aktif ? "calisiyor" : "–"))) +
    cift("Gecen sure", kacir(r.gecen_metin)) +
    cift("Duraklama", `${Math.round(r.duraklama_sn)} sn (${r.duraklama_sayisi})`) +
    cift("Tahmini", kacir(r.tahmini_metin)) +
    cift("Sapma", r.sapma_orani == null ? "–" : r.sapma_orani.toFixed(2) + "x") +
    cift("Kat edilen yol", `${(r.yol_mm / 1000).toFixed(2)} m`) +
    cift("Profil", kacir(r.profil || "–")) +
    cift("Tezgah tabani", r.taban == null ? "olculmedi" : r.taban + " mm") +
    `</div>`;

  const bl = r.bloklar || [];
  $("#raporBloklar").innerHTML =
    `<tr><th>Blok</th><th>Yol (mm)</th><th>Tahmini</th><th>Gercek</th>
     <th>Fark</th></tr>` +
    bl.map(b => {
      const f = (b.tahmin_sn != null && b.gercek_sn != null)
        ? b.gercek_sn - b.tahmin_sn : null;
      return `<tr>
        <td>${b.no}</td><td>${Math.round(b.yol_mm)}</td>
        <td>${Math.round(b.tahmin_sn)} sn</td>
        <td>${b.gercek_sn == null ? "–" : Math.round(b.gercek_sn) + " sn"}</td>
        <td class="${f == null ? "" : f > 0 ? "kotu" : "iyi"}">${
          f == null ? "–" : (f > 0 ? "+" : "") + Math.round(f) + " sn"}</td>
      </tr>`;
    }).join("");

  $("#raporOlaylar").innerHTML = (r.olaylar || []).map(o =>
    `<div class="ro-olay"><span>${new Date(o.t * 1000)
      .toLocaleTimeString("tr-TR")}</span>
     <span class="tur">${kacir(o.tur)}</span>
     <span>${kacir(o.mesaj)}</span></div>`).join("");
}
$("#rapYenile").onclick = raporCiz;

/* ================================================================== */
/* Tezgah tabani / klavye / izleme ayarlari                            */
/* ================================================================== */
$("#tabanSuAn").onclick = () => {
  const mz = S.durum && S.durum.tezgah ? S.durum.tezgah.Z : null;
  if (mz == null) {
    bildir("Once tezgah referansi alin", "hata"); return;
  }
  $("#gTaban").value = mz.toFixed(2);
  bildir("Su anki makine Z yazildi — pay eklemeyi unutmayin", "uyari");
};

$("#tabanKaydet").onclick = async () => {
  const ham = $("#gTaban").value.trim();
  const deger = ham === "" ? null : parseFloat(ham);
  if (ham !== "" && !isFinite(deger)) {
    bildir("Gecerli bir sayi girin ya da bos birakin", "hata");
    return;
  }
  // Tabanin calisma hacminin ustunde olmasi anlamsizdir; kullaniciyi uyar.
  const lim = S.ayarlar && S.ayarlar.guvenlik.yumusak_limit &&
              S.ayarlar.guvenlik.yumusak_limit.Z;
  if (deger != null && lim && deger > lim.max) {
    bildir("Taban, Z ust limitinden yuksek olamaz", "hata");
    return;
  }
  if (deger == null && $("#gTabanZorunlu").checked)
    bildir("Taban bos: denetim yapilamaz. Olcup girmeniz onerilir.", "uyari");
  await calis(() => api("/api/ayarlar", { yama: { guvenlik: {
    makine_z_taban: deger,
    makine_z_taban_zorunlu: $("#gTabanZorunlu").checked } } }),
    "Tezgah tabani kaydedildi");
  ayarlariYukle();
};

$("#klavyeKaydet").onclick = async () => {
  await calis(() => api("/api/ayarlar", { yama: { klavye: {
    dro_ctrl_gerekli: $("#kDroCtrl").checked,
    fro_azalt_tus: $("#kFroAzalt").value.trim() || "F10",
    fro_artir_tus: $("#kFroArtir").value.trim() || "F11",
    fro_adim: parseInt($("#kFroAdim").value, 10) || 5 } } }),
    "Klavye ayarlari kaydedildi");
  ayarlariYukle();
};

$("#izlemeKaydet").onclick = async () => {
  await calis(() => api("/api/ayarlar", { yama: { izleme: {
    aktif: $("#izAktif").checked,
    kok: $("#izKok").value.trim(),
    derinlik: parseInt($("#izDerinlik").value, 10) || 4,
    aralik_sn: parseFloat($("#izAralik").value) || 5,
    yeni_sayilir_dk: parseInt($("#izYeni").value, 10) || 120 } } }),
    "Izleme ayarlari kaydedildi");
  ayarlariYukle();
};

$("#izlemeSifirla").onclick = async () => {
  await calis(() => api("/api/izleme", { islem: "sifirla" }),
              "Izleme gecmisi unutuldu");
  izlemeDurumCiz();
};

function izlemeDurumCiz() {
  const iz = S.durum && S.durum.izleme;
  const el = $("#izlemeDurum");
  if (!el) return;
  if (!iz || !iz.aktif) {
    el.innerHTML = `<span class="ipucu-kucuk">Izleme kapali
      (kok klasor bos ise kendiliginden kapalidir).</span>`;
    return;
  }
  const son = iz.son_tarama
    ? new Date(iz.son_tarama * 1000).toLocaleTimeString("tr-TR") : "–";
  el.innerHTML = `<span class="ipucu-kucuk">
    ${iz.taranan_dosya} dosya izleniyor · son tarama ${son}
    ${iz.bekleyen && iz.bekleyen.length
      ? ` · <b>${iz.bekleyen.length} yeni</b>` : ""}
    ${iz.hata ? `<br><span style="color:var(--tehlike)">${kacir(iz.hata)}</span>`
              : ""}</span>`;
}

/* ---- denetim + baslatma ---- */
/* Tezgah sayfasindan da cagrilir; oradaki BASLAT, denetim gectikten sonra
   ayrica onay kutusu istemez ama yine de bir onay penceresi gosterir. */
async function denetleYap(tezgahtan = false) {
  const d = await calis(() => api("/api/program/denetle", {}));
  if (!d) return null;
  S.denetim = d;
  $("#tBaslat").disabled = !d.gecti;
  if (tezgahtan) {
    bildir(d.gecti
      ? `Denetim gecti — blok ${d.blok + 1}'den baslatilabilir`
      : "Denetim BASARISIZ", d.gecti ? "ok" : "hata",
      d.hatalar.concat(d.uyarilar));
  }
  return d;
}

async function baslatYap(tezgahtan = false) {
  if (!S.program) { bildir("Once bir program yukleyin", "uyari"); return; }
  if (!S.denetim || !S.denetim.gecti) {
    bildir("Once guvenlik denetimi yapin", "uyari");
    return;
  }
  const blok = S.program.secili_blok;
  if (!confirm(`Blok ${blok + 1}'den itibaren KESIM BASLATILACAK.\n` +
               `Takim, is parcasi ve bagliliklar hazir mi?`)) return;
  const r = await calis(() => api("/api/program/baslat", { blok, onay: true }));
  if (r) bildir("Program baslatildi: blok " + (blok + 1), "ok");
}

$("#denetleBtn").onclick = async () => {
  const d = await denetleYap(false);
  if (!d) return;
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

$("#baslatBtn").onclick = () => baslatYap(false);

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
  $("#gAcilReset").checked = a.guvenlik.acil_reset !== false;
  $("#gBirimZorla").checked = a.guvenlik.birim_zorla_mm !== false;

  // jog (Mach3 modeli)
  const jg = a.jog || {};
  $("#gJogSurucu").value = jg.surucu || "artimli";
  $("#gJogTik").value = jg.tik_ms;
  $("#gJogIleri").value = jg.ileri_sn;
  $("#gJogTampon").value = jg.adim_kuyrugu;
  $("#gJogListe").value = (jg.adim_listesi || []).join(", ");

  // tezgah tabani
  $("#gTaban").value = a.guvenlik.makine_z_taban == null
    ? "" : a.guvenlik.makine_z_taban;
  $("#gTabanZorunlu").checked = a.guvenlik.makine_z_taban_zorunlu !== false;
  // Taban TEZGAH koordinatindadir (referansa gore), ham makine
  // okumasi degil.
  const mz = S.durum && S.durum.tezgah ? S.durum.tezgah.Z : null;
  $("#tabanSuAnDeger").textContent = mz == null
    ? "(once referans alin)" : `su an tezgah Z: ${mz.toFixed(3)} mm`;

  // klavye kisayollari
  const k = a.klavye || {};
  $("#kDroCtrl").checked = k.dro_ctrl_gerekli !== false;
  $("#kFroAzalt").value = k.fro_azalt_tus || "F10";
  $("#kFroArtir").value = k.fro_artir_tus || "F11";
  $("#kFroAdim").value = k.fro_adim == null ? 5 : k.fro_adim;

  // dosya izleme
  const iz = a.izleme || {};
  $("#izAktif").checked = iz.aktif !== false;
  $("#izKok").value = iz.kok || "";
  $("#izDerinlik").value = iz.derinlik == null ? 4 : iz.derinlik;
  $("#izAralik").value = iz.aralik_sn == null ? 5 : iz.aralik_sn;
  $("#izYeni").value = iz.yeni_sayilir_dk == null ? 120 : iz.yeni_sayilir_dk;
  izlemeDurumCiz();

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
               data-ad="${kacir(k.ad)}" data-var="${k.varsayilan}"
               value="${k.kod}">
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
    acil_reset: $("#gAcilReset").checked,
    birim_zorla_mm: $("#gBirimZorla").checked,
  }, jog: {
    surucu: $("#gJogSurucu").value,
    tik_ms: Math.max(parseFloat($("#gJogTik").value) || 40, 15),
    ileri_sn: Math.max(parseFloat($("#gJogIleri").value) || 0.3, 0.1),
    adim_kuyrugu: Math.max(parseInt($("#gJogTampon").value, 10) || 5, 1),
    adim_listesi: $("#gJogListe").value.split(/[;,\s]+/)
      .map(parseFloat).filter(v => isFinite(v) && v > 0),
  } } }), "Güvenlik ayarları kaydedildi");
};

/* Bağlantı ayarlarından yalnızca DEĞİŞENLER gönderilir: progid / mach3_baslat
   sunucuda yalnızca makinenin kendi bilgisayarından değiştirilebilir; telefon
   Makine ayarlarını (hız/ivme) bunlar yüzünden kaydedemez hale gelmesin. */
function baglantiYamasi() {
  const eski = (S.ayarlar && S.ayarlar.baglanti) || {};
  const yama = { tur: $("#baglantiTur").value,
                 yoklama_hz: parseFloat($("#yoklamaHz").value) || 10 };
  if ($("#progid").value !== eski.progid) yama.progid = $("#progid").value;
  if ($("#mach3Baslat").checked !== !!eski.mach3_baslat)
    yama.mach3_baslat = $("#mach3Baslat").checked;
  return yama;
}

$("#makineKaydet").onclick = () => calis(() => api("/api/ayarlar", { yama: {
  baglanti: baglantiYamasi(),
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
  /* Yalnızca VARSAYILANDAN FARKLI kodlar ezme olarak saklanır; aksi halde
     her kayıt tüm kodları sabitler ve sonraki kod düzeltmeleri etkisiz kalır.
     Varsayılana dönenler null ile temizlenir. */
  $$("#oemKutu input").forEach(i => {
    const v = parseInt(i.value, 10);
    yama[i.dataset.alan][i.dataset.ad] =
      (isFinite(v) && String(v) !== i.dataset.var) ? v : null;
  });
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
  if (typeof durumSatiri === "function") durumSatiri(o);
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
