/* Mach3 ekranı — jog paneli, LED şeridi, G-Code penceresi, canlı konum,
   klavye. app.js'ten sonra yüklenir (api, bildir, $, $$, S, kacir ortak).

   Jog, Mach3'ün modelini izler:
     • Jog ON/OFF (Tab)           • Cont. / Step kipi (M)
     • Slow Jog Rate % ([ ])      • Step artımı, "Cycle Jog Step" (, .)
     • Shift = %100 hızlı jog     • bırakınca YAVAŞLAYARAK durma
   Tuşa basma/bırakma olayları sunucuya olduğu gibi iletilir; kipin ne
   yapacağına (sürekli hareket mi, tek adım mı) SUNUCU karar verir. */
"use strict";

const E = {
  jog: null, durum: null,
  basili: null,             // {eksen, yon, tus, kaynak}
  shiftTus: false, shiftDugme: false,
  kalp: null, bekleyen: Promise.resolve(),
  yerelDuzenleme: 0,        // slider/sayi elle degistirildi (yankiyi ezme)
  gkod: { yol: "", satirlar: [], anahtar: "", bas: 0, son: 0, aktif: 0 },
  ledImza: "", durumTimer: null,
};

const JOG_TUS = {
  ArrowLeft: ["X", -1], ArrowRight: ["X", 1],
  ArrowUp: ["Y", 1], ArrowDown: ["Y", -1],
  PageUp: ["Z", 1], PageDown: ["Z", -1],
};
const hizliMi = () => E.shiftTus || E.shiftDugme;
const aktifSekme = () => { const s = $(".sekme.aktif"); return s ? s.dataset.sekme : ""; };
/* Jog tuşları yalnızca Mach3 ekranı ve Ofsetler sekmesinde çalışır: diğer
   sayfalarda ok tuşları listeleri kaydırır, makineyi oynatmamalı. */
const jogSekmesi = () => ["tezgah", "ofsetler"].includes(aktifSekme());

/* ================================================================== */
/* Jog: basma / bırakma                                                */
/* ================================================================== */
function padIsaretle(eksen, yon) {
  $$(".jog[data-eksen]").forEach(b => b.classList.toggle("basili",
    eksen != null && b.dataset.eksen === eksen &&
    parseInt(b.dataset.yon, 10) === yon));
}

function kalpBaslat() {
  if (E.kalp) return;
  // Sunucu kalp atışı kesilirse hareketi yavaşlatıp durdurur (ölü adam).
  E.kalp = setInterval(() => { api("/api/jog/kalp").catch(() => {}); }, 100);
}
function kalpDurdur() {
  if (E.kalp) { clearInterval(E.kalp); E.kalp = null; }
}

function jogBasla(eksen, yon, tus, kaynak) {
  if (E.basili) return;                       // tek eksen
  E.basili = { eksen, yon, tus, kaynak };
  padIsaretle(eksen, yon);
  const surekli = !E.jog || (E.jog.mod || "surekli") === "surekli";
  if (surekli) kalpBaslat();
  E.bekleyen = api("/api/jog/basla", { eksen, yon, hizli: hizliMi() })
    .then((r) => {
      if (r && r.mod === "adim") kalpDurdur();
      else if (r && E.basili) kalpBaslat();
    })
    .catch((e) => {
      kalpDurdur();
      if (E.basili && E.basili.eksen === eksen) { E.basili = null; padIsaretle(null); }
      bildir(e.message, "hata", e.sebepler || []);
    });
}

/* Bırakma: basma isteği tamamlanmadan "bitir" göndermek, jogu hiç
   durdurulmamış bırakabilirdi; bu yüzden sıraya konur. */
async function jogBirak() {
  if (!E.basili) return;
  E.basili = null;
  kalpDurdur();
  padIsaretle(null);
  try { await E.bekleyen; } catch (e) { /* hata zaten gösterildi */ }
  api("/api/jog/bitir").catch(() => {});
}
function jogBitir() { jogBirak(); }           // app.js'in kullandığı ad

function shiftDegisti() {
  $("#jogShift").classList.toggle("aktif", hizliMi());
  if (E.basili && (!E.jog || E.jog.mod === "surekli"))
    api("/api/jog/hiz", { hizli: hizliMi() }).catch(() => {});
}

// Güvenlik: sayfa gizlenir / pencere odağı giderse hareket durur.
document.addEventListener("visibilitychange", () => { if (document.hidden) jogBirak(); });
window.addEventListener("blur", () => { E.shiftTus = false; jogBirak(); });
window.addEventListener("pagehide", jogBirak);

/* ---- ekran düğmeleri (fare/dokunma) ---- */
$$(".jog[data-eksen]").forEach((b) => {
  const eksen = b.dataset.eksen, yon = parseInt(b.dataset.yon, 10);
  b.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    try { b.setPointerCapture(e.pointerId); } catch (x) { /* eski tarayıcı */ }
    if (e.shiftKey) E.shiftTus = true;
    jogBasla(eksen, yon, null, "dugme");
  });
  const birak = (e) => { e.preventDefault(); jogBirak(); };
  b.addEventListener("pointerup", birak);
  b.addEventListener("pointercancel", birak);
  b.addEventListener("lostpointercapture", birak);
  b.addEventListener("contextmenu", (e) => e.preventDefault());
});
$("#jogShift").addEventListener("click", () => {
  E.shiftDugme = !E.shiftDugme;
  shiftDegisti();
});

/* ================================================================== */
/* Jog ayarları (Mach3'teki düğmeler)                                  */
/* ================================================================== */
async function jogAyar(veri) {
  const r = await calis(() => api("/api/jog/ayar", veri));
  if (r) E.jog = Object.assign(E.jog || {}, r);
  return r;
}
$("#jogAcik").onclick = () => jogAyar({ acik: !(E.jog && E.jog.acik) });
$$("#jogMod button").forEach(b => b.onclick = () => jogAyar({ mod: b.dataset.mod }));

let _yuzdeZaman = null;
function yuzdeGonder(v) {
  E.yerelDuzenleme = Date.now();
  clearTimeout(_yuzdeZaman);
  _yuzdeZaman = setTimeout(() => jogAyar({ yavas_yuzde: v }), 120);
}
$("#jogYuzde").addEventListener("input", (e) => {
  const v = parseFloat(e.target.value);
  $("#jogYuzdeSayi").value = v;
  hizMetniCiz(v);
  yuzdeGonder(v);
});
$("#jogYuzdeSayi").addEventListener("change", (e) => {
  const v = Math.min(Math.max(parseFloat(e.target.value) || 1, 0.1), 100);
  $("#jogYuzde").value = v;
  hizMetniCiz(v);
  yuzdeGonder(v);
});
$("#jogAdim").addEventListener("change", (e) => {
  E.yerelDuzenleme = Date.now();
  jogAyar({ adim: parseFloat(e.target.value) });
});
$("#jogAdimEksi").onclick = () => jogAyar({ islem: "adim_sonraki", yon: -1 });
$("#jogAdimArti").onclick = () => jogAyar({ islem: "adim_sonraki", yon: 1 });
[$("#jogYuzdeSayi"), $("#jogAdim"), $("#milDevir"), $("#mdiSatir")].forEach((el) =>
  el.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); el.blur(); }
    e.stopPropagation();                      // jog/kilit yakalayıcısına gitmesin
  }));

function hizMetniCiz(yuzde) {
  const j = E.jog;
  if (!j || !j.hiz) { $("#jogHizMetin").textContent = "–"; return; }
  const yaz = (h) => Object.entries(h).map(([e, v]) => `${e} ${Math.round(v)}`).join(" · ");
  // slider sürüklenirken sunucu henüz yeni yüzdeyi bilmez: orantıla
  const oran = (yuzde != null && j.yavas_yuzde) ? yuzde / j.yavas_yuzde : 1;
  const yavas = Object.fromEntries(Object.entries(j.hiz.yavas)
    .map(([e, v]) => [e, v * oran]));
  $("#jogHizMetin").innerHTML =
    `${yaz(yavas)} <small>mm/dk</small> · <b>Shift</b> ${yaz(j.hiz.hizli)}`;
}

/* ================================================================== */
/* Klavye                                                              */
/* ================================================================== */
document.addEventListener("keydown", (e) => {
  // DRO / referans pencereleri açıkken yalnızca Esc ile kapanırlar
  if (!$("#droKatman").classList.contains("gizli")) {
    if (e.key === "Escape") { e.preventDefault(); droKatmanKapat(); }
    return;
  }
  if (!$("#refKatman").classList.contains("gizli")) {
    if (e.key === "Escape") { e.preventDefault(); refKatmanKapat(); }
    return;
  }
  if (!$("#homeKatman").classList.contains("gizli")) {
    if (e.key === "Escape") { e.preventDefault(); homeModalKapat(); }
    return;                                   // Esc, ACİL DURDUR'a düşmesin
  }
  const yazi = YAZI_ALANI(e.target);
  if (!yazi) {
    const eksen = droKisayolu(e);
    if (eksen) { e.preventDefault(); droKatmanAc(eksen); return; }
    const yon = froKisayolu(e);
    if (yon) { e.preventDefault(); froDegistir(yon); return; }
  }
  if (e.key === "Escape") { e.preventDefault(); acilDurdur("Esc"); return; }

  if (e.key === "Shift") {
    if (!E.shiftTus) { E.shiftTus = true; shiftDegisti(); }
    return;
  }
  const serbest = !yazi && !katmanAcik() && jogSekmesi();
  if (serbest) {
    const es = JOG_TUS[e.key];
    if (es) {
      e.preventDefault();
      if (e.repeat) return;                   // Mach3: tuş tekrarı yok
      if (e.shiftKey) E.shiftTus = true;
      jogBasla(es[0], es[1], e.key, "klavye");
      return;
    }
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    if (e.key === "Tab" && aktifSekme() === "tezgah") {
      e.preventDefault(); $("#jogAcik").click(); return;
    }
    if (!e.repeat) {
      if (e.key === "m" || e.key === "M") { e.preventDefault();
        jogAyar({ mod: E.jog && E.jog.mod === "adim" ? "surekli" : "adim" }); return; }
      if (e.key === ",") { e.preventDefault(); $("#jogAdimEksi").click(); return; }
      if (e.key === ".") { e.preventDefault(); $("#jogAdimArti").click(); return; }
    }
    if (e.key === "[" || e.key === "]") {
      e.preventDefault();
      const mevcut = (E.jog && E.jog.yavas_yuzde) || 20;
      const yeni = Math.min(Math.max(mevcut + (e.key === "]" ? 5 : -5), 1), 100);
      jogAyar({ yavas_yuzde: yeni }); return;
    }
  }
  if (e.key === " " && !yazi && !katmanAcik()) {
    e.preventDefault();
    api("/api/feedhold").catch(() => {});
    return;
  }
  if (!S.kilitli || yazi) return;
  // Kilit açıkken geri kalan HER tuş yutulur (Alt, Tab, Win kombinasyonları...)
  e.preventDefault();
  e.stopPropagation();
}, true);

document.addEventListener("keyup", (e) => {
  // Bırakma, hareketi durduran yoldur: hiçbir koşula bağlanamaz.
  if (E.basili && E.basili.tus && e.key === E.basili.tus) {
    e.preventDefault(); jogBirak(); return;
  }
  if (JOG_TUS[e.key]) { e.preventDefault(); return; }
  if (e.key === "Shift") { E.shiftTus = false; shiftDegisti(); }
}, true);

/* ================================================================== */
/* Çalıştırma düğmeleri                                                */
/* ================================================================== */
async function cycleStart() {
  const d = E.durum || {};
  if (d.estop) return bildir("Mach3 RESET durumunda — önce Reset'i kaldırın", "hata");
  if (d.led && d.led.duraklatildi) {          // Feed Hold'dan devam
    await calis(() => api("/api/devam"), "Devam");
    return;
  }
  if (!S.program) return bildir("Önce bir G-Code dosyası açın", "uyari");
  // Güvenli başlatma: denetim → onay → başlat
  if (!S.denetim || !S.denetim.gecti || S.denetim.blok !== S.program.secili_blok) {
    const sonuc = await denetleYap(true);
    if (!sonuc || !sonuc.gecti) return;
  }
  baslatYap(true);
}
$("#m3CycleStart").onclick = cycleStart;
$("#m3Reset").onclick = () => calis(() => api("/api/makine", { ad: "reset" }));
$("#m3Rewind").onclick = () => calis(() => api("/api/makine", { ad: "rewind" }), "Rewind");
$("#m3TekBlok").onclick = () => calis(() => api("/api/makine", { ad: "tek_blok" }))
  .then((r) => { if (r) $("#m3TekBlok").classList.toggle("aktif"); });
$("#m3SifiraGit").onclick = () =>
  calis(() => api("/api/sifira_git", { eksenler: ["X", "Y"] }), "X0 Y0'a gidiliyor");
$("#guvenliZBtn").onclick = () =>
  calis(() => api("/api/guvenli_z"), "Güvenli yüksekliğe çıkılıyor");
$("#froSifirla").onclick = () => calis(() => api("/api/fro", { deger: 100 }));
const mil = (ad) => calis(() => api("/api/makine",
  { ad, devir: parseFloat($("#milDevir").value) || null }));
$("#milCw").onclick = () => mil("mil_cw");
$("#milCcw").onclick = () => mil("mil_ccw");
$("#milDur").onclick = () => mil("mil_dur");
$("#sogSel").onclick = () => calis(() => api("/api/makine", { ad: "sogutma_sel" }));
$("#sogSis").onclick = () => calis(() => api("/api/makine", { ad: "sogutma_sis" }));
$("#sogKapat").onclick = () => calis(() => api("/api/makine", { ad: "sogutma_kapat" }));

/* ================================================================== */
/* Durum satırı (Mach3'ün alt mesaj çubuğu)                            */
/* ================================================================== */
function durumSatiri(o) {
  const goster = ["hata", "guvenlik", "uyari", "acil", "jog", "program",
                  "kontrol", "referans", "sifirla"];
  if (!goster.includes(o.tur)) return;
  const el = $("#m3Durum");
  const saat = new Date(o.t * 1000).toLocaleTimeString("tr-TR");
  el.className = "m3-durum " + (["hata", "acil"].includes(o.tur) ? "hata"
    : ["guvenlik", "uyari"].includes(o.tur) ? "uyari" : "");
  el.innerHTML = `<span class="saat">${saat}</span><span>${kacir(o.mesaj)}</span>`;
}

/* Bildirimler (reddedilen jog, hata...) Mach3'teki gibi durum satırında da kalır. */
function durumMetni(mesaj, tur, sebepler) {
  const el = $("#m3Durum");
  if (!el) return;
  const saat = new Date().toLocaleTimeString("tr-TR");
  el.className = "m3-durum " + (tur === "hata" ? "hata" : tur === "uyari" ? "uyari" : "");
  el.innerHTML = `<span class="saat">${saat}</span><span>${kacir(mesaj)}` +
    `${sebepler && sebepler.length ? " — " + kacir(sebepler.join("; ")) : ""}</span>`;
}

/* ================================================================== */
/* LED şeridi                                                          */
/* ================================================================== */
function ledCiz(d) {
  const L = d.led || {};
  const ogeler = [];
  const led = (ad, yanik, sinif = "") => ogeler.push({ ad, yanik: !!yanik, sinif });
  led("RESET", d.estop, "kirmizi" + (d.estop ? " yanip" : ""));
  led("IDLE", L.bosta, "yesil");
  led("RUN", L.calisiyor || d.calisiyor, "yesil");
  led("PAUSE", L.duraklatildi, "sari");
  led("CV", L.cv, L.cv === false ? "sari" : "yesil");
  led("SOFT", L.yumusak_limit, "sari");
  if (L.limit_gecersiz) led("LİMİT OVR", true, "kirmizi");
  const j = d.jog || {};
  led(!j.acik ? "JOG OFF" : j.mod === "adim" ? "JOG STEP" : "JOG CONT",
      j.acik, j.acik ? "mavi" : "kirmizi");
  led(d.birim === "inch" ? "G20 İNÇ" : d.birim === "mm" ? "mm" : "birim ?",
      d.birim != null, d.birim === "inch" ? "sari" : "yesil");
  const r = d.referans || {};
  led(r.alindi ? "REF OK" : "REF YOK", r.alindi, r.alindi ? "yesil" : "sari");
  const hm = d.profiller || {};
  led(hm.aktif_gercek ? "HOME: " + hm.aktif_gercek : "HOME –", !!hm.aktif_gercek, "mavi");
  if (d.tur === "simulasyon") led("SİM", true, "mor");
  const imza = JSON.stringify(ogeler);
  if (imza === E.ledImza) return;
  E.ledImza = imza;
  $("#m3Ledler").innerHTML = ogeler.map(o =>
    `<span class="m3-led ${o.yanik ? "yanik" : ""} ${o.sinif}"><i></i>${kacir(o.ad)}</span>`
  ).join("");
}

/* ================================================================== */
/* G-Code penceresi                                                    */
/* ================================================================== */
const GKOD_ONCE = 40, GKOD_SONRA = 160;

async function gkodYukle() {
  const r = await api("/api/program/satirlar", { bas: 0, adet: 20000 }).catch(() => null);
  if (!r) return;
  E.gkod.yol = r.yol;
  E.gkod.satirlar = r.satirlar || [];
  E.gkod.toplam = r.toplam || 0;
  $("#gkodBilgi").textContent = r.ad ? `${r.ad} · ${r.toplam} satır` : "";
  gkodCiz(true);
}

function gkodCiz(zorla) {
  const g = E.gkod, kap = $("#gkod");
  if (!g.satirlar.length) {
    if (zorla) kap.innerHTML = `<div class="bos-mesaj">Program yok.</div>`;
    return;
  }
  const aktif = Math.round((E.durum && E.durum.satir) || 0);   // 1 tabanlı
  const merkez = aktif > 0 ? aktif - 1 : 0;
  const pencereDisi = merkez < g.bas || merkez >= g.son - 10;
  if (zorla || pencereDisi || !kap.firstChild) {
    g.bas = Math.max(0, merkez - GKOD_ONCE);
    g.son = Math.min(g.satirlar.length, merkez + GKOD_SONRA);
    kap.innerHTML = g.satirlar.slice(g.bas, g.son).map((s, i) => {
      const no = g.bas + i + 1;
      return `<div class="gk" data-n="${no}"><span class="no">${no}</span>` +
             `<span class="kod">${kacir(s) || "&nbsp;"}</span></div>`;
    }).join("");
    g.aktif = 0;
  }
  if (aktif !== g.aktif) {
    const eski = kap.querySelector(".gk.aktif");
    if (eski) eski.classList.remove("aktif");
    const yeni = kap.querySelector(`.gk[data-n="${aktif}"]`);
    if (yeni) {
      yeni.classList.add("aktif");
      kap.scrollTop = Math.max(0, yeni.offsetTop - kap.clientHeight / 3);
    }
    kap.querySelectorAll(".gk").forEach((el) =>
      el.classList.toggle("bitti", aktif > 0 && parseInt(el.dataset.n, 10) < aktif));
    g.aktif = aktif;
  }
}

function gkodAnahtar(d) {
  const p = d.program;
  return (p ? p.yol : "") + "|" + (d.calisiyor ? "c" : "-") + "|" +
         (p ? p.secili_blok : "");
}

/* ================================================================== */
/* Ana çizim                                                           */
/* ================================================================== */
function ekranCiz(d) {
  E.durum = d;
  E.jog = d.jog || E.jog;
  const j = d.jog || {};
  const yerelMesgul = Date.now() - E.yerelDuzenleme < 1200;

  // Jog ON/OFF + kip
  const ac = $("#jogAcik");
  ac.classList.toggle("aktif", !!j.acik);
  ac.lastChild.textContent = j.acik ? "Jog ON" : "Jog OFF";
  $$("#jogMod button").forEach(b => b.classList.toggle("aktif", b.dataset.mod === j.mod));
  document.body.classList.toggle("jog-kapali", !j.acik);
  document.body.classList.toggle("estop", !!d.estop);

  if (!yerelMesgul) {
    if (j.yavas_yuzde != null) {
      if (document.activeElement !== $("#jogYuzde")) $("#jogYuzde").value = j.yavas_yuzde;
      if (document.activeElement !== $("#jogYuzdeSayi")) $("#jogYuzdeSayi").value = j.yavas_yuzde;
    }
    if (j.adim != null && document.activeElement !== $("#jogAdim")) $("#jogAdim").value = j.adim;
    hizMetniCiz();
  }
  // Step listesi
  const imza = (j.adim_listesi || []).join("|") + "@" + j.adim;
  if (imza !== E.adimImza) {
    E.adimImza = imza;
    $("#adimlar").innerHTML = (j.adim_listesi || []).map(a =>
      `<button class="adim${Math.abs(a - j.adim) < 1e-9 ? " aktif" : ""}" data-a="${a}">${a}</button>`
    ).join("");
    $$("#adimlar .adim").forEach(b => b.onclick = () => jogAyar({ adim: parseFloat(b.dataset.a) }));
  }

  // Jog bilgi satırı
  const bilgi = $("#jogBilgi");
  let metin, sinif = "";
  if (d.estop) { metin = "RESET — hareket yok. Reset düğmesine basın."; sinif = "hata"; }
  else if (!j.acik) { metin = "Jog KAPALI — açmak için Jog ON (Tab)"; sinif = "uyari"; }
  else if (j.aktif) {
    metin = `JOG ${j.eksen}${j.yon > 0 ? "+" : "−"} · ${Math.round(j.feed || 0)} mm/dk` +
            `${j.hizli ? " · Shift" : ""} · ${j.surucu === "mach3" ? "Mach3 yerel" : "artımlı"}`;
    sinif = "aktif";
  } else if (j.son_sebep && /limit|reset|kalp|guvenlik|hata|ters/i.test(j.son_sebep)) {
    metin = "Son jog durdu: " + j.son_sebep; sinif = "uyari";
  } else {
    metin = j.mod === "adim"
      ? `Step ${j.adim} mm · bir basış = bir adım`
      : "Continuous · tuşu basılı tutun";
  }
  bilgi.textContent = metin;
  bilgi.className = "m3-jog-bilgi " + sinif;

  // Reset düğmesi: E-Stop'ta yanıp söner
  $("#m3Reset").classList.toggle("yanip", !!d.estop);
  $("#m3Reset").textContent = d.estop ? "RESET ▶ KALDIR" : "RESET";
  $("#m3CycleStart").textContent =
    d.led && d.led.duraklatildi ? "CYCLE START ▶ DEVAM" : "CYCLE START";

  ledCiz(d);

  // G-Code: dosya/çalışma değişince yeniden yükle, her karede satırı güncelle
  const anahtar = gkodAnahtar(d);
  if (anahtar !== E.gkod.anahtar) {
    E.gkod.anahtar = anahtar;
    if (d.program) gkodYukle();
    else { E.gkod.satirlar = []; E.gkod.yol = ""; gkodCiz(true); $("#gkodBilgi").textContent = ""; }
  } else {
    gkodCiz(false);
  }
  if (typeof ozDurum === "function") ozDurum(d);   // onizleme.js: sinir + canli konum
}

/* ================================================================== */
/* Tanılama (Ayarlar sayfası)                                          */
/* ================================================================== */
function tanilamaYenile() {
  const d = E.durum || S.durum;
  const el = $("#tanilamaKutu");
  if (!d) { el.textContent = "–"; return; }
  const satir = (a, b, s = "") => `<div><span>${a}</span><b class="${s}">${b}</b></div>`;
  const j = d.jog || {};
  const L = d.led || {};
  const esl = (S.ayarlar && S.ayarlar.jog && S.ayarlar.jog.yon_esleme) || {};
  const eslMetin = Object.entries(esl).map(([e, m]) =>
    `${e}: +→${m["1"]}, −→${m["-1"]}`).join(" · ");
  el.innerHTML =
    satir("Bağlantı", d.bagli ? (d.tur === "simulasyon" ? "simülasyon" : "Mach3 (COM)") : "yok",
          d.bagli ? "" : "kotu") +
    satir("Reset / E-Stop", d.estop ? "AKTİF" : "yok", d.estop ? "kotu" : "") +
    satir("G-code birimi", d.birim === "mm" ? "mm (G21)" :
          d.birim === "inch" ? "İNÇ (G20) — hareket öncesi G21 zorlanır" : "okunamadı",
          d.birim === "inch" ? "uyari" : "") +
    satir("CV (sabit hız) kipi", L.cv == null ? "?" : L.cv ? "açık" : "KAPALI (G61)",
          L.cv === false ? "uyari" : "") +
    satir("Mach3 jog kipi", L.jog_adim ? "Step" : L.jog_surekli ? "Continuous" : "?") +
    satir("Mach3 yumuşak limit", L.yumusak_limit ? "açık" : "kapalı") +
    satir("Hareket kuyruğu", d.kuyruk == null ? "?" : d.kuyruk) +
    satir("Yerel jog (JogOn/JogOff)", j.yerel_var ? "var" : "yok", j.yerel_var ? "" : "uyari") +
    satir("Yerel jog doğrulandı", j.mach3_dogrulandi ? "evet — " + eslMetin : "hayır",
          j.mach3_dogrulandi ? "iyi" : "") +
    satir("Etkin jog sürücüsü",
          j.tercih_surucu === "mach3" && j.mach3_dogrulandi ? "Mach3 yerel" : "artımlı (MDI zinciri)");
}
$("#tanilamaYenile").onclick = tanilamaYenile;

$("#jogDogrulaBtn").onclick = async () => {
  if (!E.durum || !E.durum.bagli) return bildir("Mach3 bağlantısı yok", "hata");
  if (!confirm(
    "MACH3 JOG DOĞRULAMA\n\n" +
    "Her eksen (X, Y, Z) iki yönde ~0,35 sn, %5 hızla kısa hareket eder " +
    "(birkaç milimetre). Mach3 Continuous jog kipinde, Reset kapalı ve " +
    "eksenlerin iki yönde de ≥ 6 mm boşluğu olmalı.\n\n" +
    "Takım ve malzeme güvenli mesafede mi? Devam edilsin mi?")) return;
  bildir("Doğrulama çalışıyor… (birkaç saniye)", "uyari");
  const r = await calis(() => api("/api/jog/dogrula", { eksenler: ["X", "Y", "Z"] }),
                        "Mach3 yerel jog doğrulandı");
  if (r) { await ayarlariYukle(); tanilamaYenile(); }
};

/* ================================================================== */
/* Önizleme paneli: aç/kapat, araçlar                                  */
/* ================================================================== */
function ozKatla(kapat) {
  const kap = $("#m3Yol");
  kap.classList.toggle("katli", kapat);
  $("#ozKatla").textContent = kapat ? "▸" : "▾";
  $("#ozKatla").setAttribute("aria-expanded", String(!kapat));
  try { localStorage.setItem("m3_oz_katli", kapat ? "1" : "0"); } catch (e) { /* özel mod */ }
  if (!kapat) setTimeout(ozGorunurYenile, 30);    // gizliyken çizilemez: şimdi çiz
}
$("#ozKatla").onclick = () => ozKatla(!$("#m3Yol").classList.contains("katli"));

/* ================================================================== */
/* Başlangıç                                                           */
/* ================================================================== */
$("#jogShift").classList.remove("aktif");
new OzGorunum("#onizlemeMini", "#olcuSeritMini");
new OzGorunum("#onizleme", "#olcuSerit");
ozAraclariKur();
let _katli = "0";
try { _katli = localStorage.getItem("m3_oz_katli") || "0"; } catch (e) { /* */ }
ozKatla(_katli === "1");
