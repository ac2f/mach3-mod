/* Home noktaları — adlandırılmış iş sıfırları.

   Her Home, bir malzemenin/işin sıfır noktasıdır ve TEZGÂH koordinatıyla
   saklanır (Mach3 yeniden açılsa bile referans alındıktan sonra aynı fiziksel
   noktaya döner). Bu dosya: Mach3 ekranındaki HOME bölümü, Ofsetler
   sekmesindeki yönetim tablosu ve kaydet/düzenle penceresi.
   app.js'ten sonra yüklenir (api, bildir, calis, $, $$, S, kacir ortak). */
"use strict";

const H = { veri: null, imza: "", sec: "", duzen: null };

const homeSayi = (v) => (v == null ? "" : (+v).toFixed(3));
const homeAdi = (ad) => encodeURIComponent(ad);

/* ================================================================== */
/* Çizim (her durum karesinde çağrılır; yalnızca değişince yeniden çizer) */
/* ================================================================== */
function homeCiz(pr) {
  if (!pr) return;
  H.veri = pr;
  const imza = JSON.stringify([pr.liste, pr.aktif_gercek, pr.mevcut]);
  if (imza === H.imza) return;
  H.imza = imza;
  const liste = pr.liste || [];

  // ---- ana ekran seçim kutusu
  if (!H.sec || !liste.some(h => h.ad === H.sec)) H.sec = pr.aktif_gercek || "";
  const sel = $("#homeSec");
  sel.innerHTML = `<option value="">— Home seç (${liste.length}) —</option>` +
    liste.map(h => `<option value="${kacir(h.ad).replace(/"/g, "&quot;")}"` +
      `${h.ad === H.sec ? " selected" : ""}>` +
      `${h.ad === pr.aktif_gercek ? "● " : ""}${kacir(h.ad)}</option>`).join("");
  sel.value = H.sec;
  $("#homeDurum").textContent = pr.aktif_gercek
    ? "aktif: " + pr.aktif_gercek
    : (liste.length ? "hiçbiri aktif değil" : "henüz yok");
  $("#homeUygula").disabled = $("#homeUygulaGit").disabled = !H.sec;

  // ---- Ofsetler sekmesi
  const m = pr.mevcut || {};
  $("#homeMevcut").textContent = Object.keys(m).length
    ? "mevcut sıfır (tezgâh): " + Object.entries(m)
        .map(([e, v]) => `${e} ${v.toFixed(3)}`).join(" · ")
    : "mevcut sıfır okunamıyor (referans yok / bağlantı yok)";

  if (!liste.length) {
    $("#homeTablo").innerHTML =
      `<div class="ipucu-kucuk">Henüz Home yok. Bir malzemenin sıfırını Mach3 ekranında
       ayarlayıp <b>+ Kaydet…</b> deyin.</div>`;
    return;
  }
  $("#homeTablo").innerHTML = `
    <table class="tablo home-t">
      <tr><th>Home</th><th>X</th><th>Y</th><th>Z</th><th>Durum</th><th></th></tr>
      ${liste.map(h => {
        const ad = homeAdi(h.ad);
        const etiket = h.uyumlu
          ? `<span class="home-rozet aktif">● AKTİF</span>`
          : h.uyumlu === false
            ? `<span class="home-rozet" title="Mevcut sıfır bu Home'dan farklı: ${
                kacir(Object.entries(h.fark || {}).map(([e, v]) =>
                  `${e} ${v > 0 ? "+" : ""}${v}`).join(", "))} mm">uygulanmadı</span>`
            : `<span class="home-rozet">–</span>`;
        return `<tr class="${h.uyumlu ? "home-aktif" : ""}">
          <td class="home-ad"><b>${kacir(h.ad)}</b>${
            h.malzeme ? `<span class="home-alt">${kacir(h.malzeme)}</span>` : ""}${
            h.not ? `<span class="home-alt">${kacir(h.not)}</span>` : ""}${
            h.eski ? `<span class="home-rozet uyari" title="Eski sürümden kalma kayıt: koordinatları doğrulayın">eski kayıt</span>` : ""}</td>
          ${["X", "Y", "Z"].map(e =>
            `<td class="home-sayi">${h.tezgah[e] == null ? "–" : homeSayi(h.tezgah[e])}</td>`).join("")}
          <td>${etiket}</td>
          <td class="home-islem">
            <button class="mini" data-hi="uygula" data-ad="${ad}">Uygula</button>
            <button class="mini" data-hi="git" data-ad="${ad}" title="Uygula, sonra sıfıra git">Uygula + git</button>
            <button class="mini" data-hi="duzenle" data-ad="${ad}">Düzenle</button>
            <button class="mini tehlike-mini" data-hi="sil" data-ad="${ad}">Sil</button>
          </td></tr>`;
      }).join("")}
    </table>`;
}

/* ================================================================== */
/* Uygula / sil                                                        */
/* ================================================================== */
function homeBul(ad) {
  return ((H.veri && H.veri.liste) || []).find(h => h.ad === ad);
}

async function homeUygula(ad, git) {
  const h = homeBul(ad);
  if (!h) return bildir("Home bulunamadı", "hata");
  const tz = Object.entries(h.tezgah).map(([e, v]) => `${e} ${v.toFixed(3)}`).join(" · ");
  let metin = `"${ad}" Home'u uygulanacak.\n\nİş sıfırı şu tezgâh koordinatına ` +
    `taşınır: ${tz}\n(DRO değerleri değişir; makine hareket etmez.)`;
  if (git) metin += "\n\nArdından kafa X0 Y0'a gidecek (önce güvenli Z).";
  if (h.eski) metin += "\n\n⚠ Eski sürümden kalma kayıt: koordinatları doğrulayın.";
  if (!confirm(metin)) return;
  const r = await calis(() => api("/api/home/uygula", { ad, git: !!git }),
                        `Home uygulandı: ${ad}`);
  if (r && r.git_hata)
    bildir("Home uygulandı, ama sıfıra gidilemedi: " + r.git_hata, "uyari",
           r.sebepler || []);
}

async function homeSil(ad) {
  if (!confirm(`"${ad}" Home'u silinsin mi?\n(Mach3'teki mevcut iş sıfırı değişmez.)`)) return;
  await calis(() => api("/api/home/sil", { ad }), `Home silindi: ${ad}`);
}

$("#homeTablo").addEventListener("click", (e) => {
  const b = e.target.closest("[data-hi]");
  if (!b) return;
  const ad = decodeURIComponent(b.dataset.ad);
  const islem = b.dataset.hi;
  if (islem === "uygula") homeUygula(ad, false);
  else if (islem === "git") homeUygula(ad, true);
  else if (islem === "duzenle") homeModalAc("duzenle", homeBul(ad));
  else if (islem === "sil") homeSil(ad);
});

/* ---- ana ekran düğmeleri ---- */
$("#homeSec").addEventListener("change", (e) => {
  H.sec = e.target.value;
  $("#homeUygula").disabled = $("#homeUygulaGit").disabled = !H.sec;
});
$("#homeUygula").onclick = () => H.sec && homeUygula(H.sec, false);
$("#homeUygulaGit").onclick = () => H.sec && homeUygula(H.sec, true);
$("#homeKaydet").onclick = () => homeModalAc("yeni");
$("#homeYeniBtn").onclick = () => homeModalAc("yeni");
$("#homeYonet").onclick = () => {
  sekmeGec("ofsetler");
  const k = $("#homeKart");
  if (k) k.scrollIntoView({ block: "start", behavior: "smooth" });
};

/* ================================================================== */
/* Kaydet / düzenle penceresi                                          */
/* ================================================================== */
const HOME_EKSEN = ["X", "Y", "Z"];

function homeMevcutAl() {
  const m = (H.veri && H.veri.mevcut) || {};
  if (!Object.keys(m).length) {
    bildir("Mevcut sıfır okunamıyor: bağlantı ya da tezgâh referansı yok", "uyari");
    return false;
  }
  HOME_EKSEN.forEach(e => { $("#home" + e).value = m[e] == null ? "" : m[e].toFixed(3); });
  $("#homeMevcutBilgi").textContent = "şu anki iş sıfırı alındı";
  return true;
}

function homeModalAc(mod, kayit) {
  H.duzen = mod === "duzenle" ? kayit.ad : null;
  $("#homeBaslik").textContent = mod === "duzenle"
    ? `Home düzenle: ${kayit.ad}` : "Mevcut sıfırı Home olarak kaydet";
  $("#homeTamam").textContent = mod === "duzenle" ? "Değişiklikleri kaydet" : "Kaydet";
  $("#homeAd").value = kayit ? kayit.ad : "";
  $("#homeMalzeme").value = kayit ? kayit.malzeme : "";
  $("#homeBoy").value = kayit && kayit.takim_boyu != null ? kayit.takim_boyu : "";
  $("#homeGZ").value = kayit && kayit.guvenli_z != null ? kayit.guvenli_z : "";
  $("#homeNot").value = kayit ? kayit.not : "";
  $("#homeMevcutBilgi").textContent = "";
  $("#homeUyari").classList.add("gizli");
  if (kayit) {
    HOME_EKSEN.forEach(e => { $("#home" + e).value = kayit.tezgah[e] == null ? "" : kayit.tezgah[e]; });
  } else {
    HOME_EKSEN.forEach(e => { $("#home" + e).value = ""; });
    homeMevcutAl();
  }
  $("#homeKatman").classList.remove("gizli");
  setTimeout(() => { const a = $("#homeAd"); a.focus(); a.select(); }, 30);
}

function homeModalKapat() { $("#homeKatman").classList.add("gizli"); }

async function homeModalKaydet() {
  const ad = $("#homeAd").value.trim().replace(/\s+/g, " ");
  if (!ad) return bildir("Takma ad gerekli", "hata");
  const tezgah = {}, bos = [];
  for (const e of HOME_EKSEN) {
    const ham = $("#home" + e).value.trim();
    if (ham === "") { tezgah[e] = null; bos.push(e); continue; }
    const v = parseFloat(ham);
    if (!isFinite(v)) return bildir(`${e} için geçerli bir sayı girin`, "hata");
    tezgah[e] = v;
  }
  if (bos.length === HOME_EKSEN.length)
    return bildir("En az bir eksenin koordinatı gerekli", "hata");
  const boyHam = $("#homeBoy").value.trim(), gzHam = $("#homeGZ").value.trim();
  const ortak = {
    malzeme: $("#homeMalzeme").value.trim(), not: $("#homeNot").value.trim(),
    takim_boyu: boyHam === "" ? null : parseFloat(boyHam),
    guvenli_z: gzHam === "" ? null : parseFloat(gzHam),
  };
  let r;
  if (H.duzen) {
    r = await calis(() => api("/api/home/duzenle", Object.assign(
      { ad: H.duzen, yeni_ad: ad, tezgah }, ortak)), `Home güncellendi: ${ad}`);
  } else {
    const var_ = ((H.veri && H.veri.liste) || []).find(
      h => h.ad.toLowerCase() === ad.toLowerCase());
    if (var_ && !confirm(`"${var_.ad}" adında bir Home zaten var.\nÜzerine yazılsın mı?`))
      return;
    const gonder = {};
    HOME_EKSEN.forEach(e => { if (tezgah[e] != null) gonder[e] = tezgah[e]; });
    r = await calis(() => api("/api/home/kaydet", Object.assign(
      { ad, tezgah: gonder, uzerine_yaz: !!var_ }, ortak)), `Home kaydedildi: ${ad}`);
  }
  if (r) {
    homeModalKapat();
    if (r.uyarilar && r.uyarilar.length) bildir("Dikkat", "uyari", r.uyarilar);
    H.sec = ad;
    H.imza = "";                       // listeyi yeniden çiz
    if (r.homeler) homeCiz(r.homeler);
  }
}

$("#homeMevcutAl").onclick = homeMevcutAl;
$("#homeIptal").onclick = homeModalKapat;
$("#homeTamam").onclick = homeModalKaydet;
$("#homeKatman").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && e.target.tagName !== "BUTTON") { e.preventDefault(); homeModalKaydet(); }
  if (e.key === "Escape") { e.preventDefault(); homeModalKapat(); }
  e.stopPropagation();
});
