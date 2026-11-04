/* Takım yolu önizlemesi — paylaşılan çizici.

   Hem ana pencerede (Mach3 ekranı ve Program sekmesi) hem de ayrı önizleme
   penceresinde (onizleme.html) kullanılır; bu yüzden app.js'e BAĞIMLI DEĞİLDİR
   (kendi yardımcıları ozQ/ozQQ'dur).

   Katmanlar (neyin gösterileceği) tek yerde saklanır (localStorage) ve
   pencereler arasında canlı eşitlenir: birinde bir katmanı kapatınca
   diğerinde de kapanır.

   VEKTÖR: takımın güvenli Z'nin ALTINA indiği andan tekrar güvenli Z'ye
   çıktığı ana kadarki yol. Her güvenli Z yükselişi bir vektörü kapatır;
   numaralar dosyadaki gerçek gezinme sırasıdır (sunucu hesaplar:
   gcode.Program.vektorler). */
"use strict";

const ozQ = (s, k = document) => k.querySelector(s);
const ozQQ = (s, k = document) => [...k.querySelectorAll(s)];
const ozKac = s => String(s == null ? "" : s)
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

const OZ_KATMANLAR = [
  { id: "kesim", ad: "Kesim yolu", acik: true,
    ip: "Kesim blokları (seçili blok yeşil, sonrakiler mavi)" },
  { id: "tasima", ad: "Taşıma (G0)", acik: false,
    ip: "Bloklar arası boşta gidişler" },
  { id: "blok", ad: "Blok no", acik: false,
    ip: "Kesim sırası listesindeki blok numaraları" },
  { id: "vektor", ad: "Vektör sırası", acik: true,
    ip: "Güvenli Z'ye her yükselişle biten kesim bir vektördür; numara gezinme sırasıdır" },
  { id: "gecis", ad: "Z geçişleri", acik: true,
    ip: "Bir vektörün sonundan sonrakinin başına güvenli Z'de geçiş (oklu)" },
  { id: "vuzun", ad: "Vektör uzunluğu", acik: false,
    ip: "Her vektörün yol uzunluğu (mm)" },
  { id: "yon", ad: "Yön", acik: false,
    ip: "Her vektörün başlangıç yönü" },
  { id: "kenar", ad: "Kenar boşlukları", acik: true,
    ip: "Takım yolunun her kenarından tezgâh sınırına kalan mesafe" },
  { id: "zarf", ad: "G0 zarfı", acik: false,
    ip: "Kenar boşluklarını boşta (G0) hareketler dahil tüm zarfa göre hesapla" },
  { id: "sinir", ad: "Tezgâh sınırı", acik: true,
    ip: "Yumuşak limitlerin iş koordinatındaki yeri" },
  { id: "olcu", ad: "Kesim alanı", acik: false,
    ip: "Kesim alanı kutusu ve genişlik/yükseklik" },
  { id: "sifir", ad: "İş sıfırı", acik: true, ip: "X0 Y0" },
  { id: "konum", ad: "Takım", acik: true, ip: "Takımın canlı konumu" },
  { id: "izgara", ad: "Izgara", acik: false, ip: "Ölçekli ızgara" },
];

/* ================================================================== */
/* Katman ayarları (pencereler arası eşitlenir)                        */
/* ================================================================== */
const OzAyar = (() => {
  const ANAHTAR = "m3_oz_katmanlar";
  let durum = {};
  const dinleyiciler = [];

  function oku() {
    try { return JSON.parse(localStorage.getItem(ANAHTAR) || "{}") || {}; }
    catch (e) { return {}; }
  }
  function yukle() {
    const k = oku();
    durum = {};
    OZ_KATMANLAR.forEach(l => { durum[l.id] = (l.id in k) ? !!k[l.id] : l.acik; });
  }
  function kaydet() {
    try { localStorage.setItem(ANAHTAR, JSON.stringify(durum)); } catch (e) { /* özel mod */ }
  }
  function bildir() { dinleyiciler.forEach(f => f()); }
  yukle();
  window.addEventListener("storage", (e) => {
    if (e.key === ANAHTAR) { yukle(); bildir(); }
  });
  return {
    al: (id) => !!durum[id],
    hepsi: () => Object.assign({}, durum),
    ayarla(id, v) { durum[id] = !!v; kaydet(); bildir(); },
    topluAyarla(v) { OZ_KATMANLAR.forEach(l => { durum[l.id] = !!v; }); kaydet(); bildir(); },
    varsayilan() { OZ_KATMANLAR.forEach(l => { durum[l.id] = l.acik; }); kaydet(); bildir(); },
    dinle(f) { dinleyiciler.push(f); },
  };
})();

const OZ_KAYIT = [];                     // bu sayfadaki çizici örnekleri

/* Doğru parçasını dikdörtgene kırp (Liang–Barsky); tamamen dışındaysa null. */
function ozKirp(a, b, r) {
  let t0 = 0, t1 = 1;
  const dx = b[0] - a[0], dy = b[1] - a[1];
  const p = [-dx, dx, -dy, dy];
  const q = [a[0] - r.x0, r.x1 - a[0], a[1] - r.y0, r.y1 - a[1]];
  for (let i = 0; i < 4; i++) {
    if (p[i] === 0) { if (q[i] < 0) return null; continue; }
    const t = q[i] / p[i];
    if (p[i] < 0) { if (t > t1) return null; if (t > t0) t0 = t; }
    else { if (t < t0) return null; if (t < t1) t1 = t; }
  }
  return [[a[0] + t0 * dx, a[1] + t0 * dy], [a[0] + t1 * dx, a[1] + t1 * dy]];
}

/* ================================================================== */
/* Çizici                                                              */
/* ================================================================== */
class OzGorunum {
  constructor(kapSecici, bilgiSecici) {
    this.kap = ozQ(kapSecici);
    this.bilgi = bilgiSecici ? ozQ(bilgiSecici) : null;
    this.p = null;                       // program bilgisi (/api/program/bilgi)
    this.sinir = null;                   // d.sinir_is
    this.konum = null;                   // {X, Y} iş koordinatı
    this.v = null;                       // görünüm (svg koordinatı: x0, y0, w, h)
    this.programAnahtar = "";
    this.sinirImza = "";
    this.kirli = true;
    this.surukle = null;
    OzAyar.dinle(() => this.ciz());
    OZ_KAYIT.push(this);
  }

  /* -- veri girişi -------------------------------------------------- */
  program(p) {
    this.p = (p && p.bloklar && p.bloklar.length) ? p : null;
    this.ciz();
  }
  durum(d) {
    const imza = JSON.stringify(d.sinir_is || null);
    const x = d.konum && d.konum.X, y = d.konum && d.konum.Y;
    this.konum = (x == null || y == null) ? null : { X: x, Y: y };
    if (imza !== this.sinirImza) {
      this.sinirImza = imza;
      this.sinir = d.sinir_is || null;
      this.ciz();
    } else {
      this.konumGuncelle();
    }
  }
  gorunur() { return !!this.kap && this.kap.offsetParent !== null; }

  /* -- geometri ------------------------------------------------------ */
  sinirlar() {
    const p = this.p;
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    p.bloklar.forEach(b => {
      x0 = Math.min(x0, b.bbox[0]); y0 = Math.min(y0, b.bbox[1]);
      x1 = Math.max(x1, b.bbox[2]); y1 = Math.max(y1, b.bbox[3]);
    });
    // iş sıfırı (X0 Y0) hep görünsün
    x0 = Math.min(x0, 0); y0 = Math.min(y0, 0);
    x1 = Math.max(x1, 0); y1 = Math.max(y1, 0);
    const yol = [x0, y0, x1, y1];
    const A = this.sinir && this.sinir.aralik;
    let tum = yol;
    if (A && A.X && A.Y)
      tum = [Math.min(x0, A.X[0]), Math.min(y0, A.Y[0]),
             Math.max(x1, A.X[1]), Math.max(y1, A.Y[1])];
    return { yol, tum };
  }

  sigdir(mod) {
    if (!this.p) return;
    const s = this.sinirlar();
    const b = s[mod] || s.yol;
    this.sigdirMod = s[mod] ? mod : "yol";
    const pay = Math.max(b[2] - b[0], b[3] - b[1]) * 0.16 + 2;
    this.v = { x0: b[0] - pay, y0: -(b[3] + pay),
               w: b[2] - b[0] + 2 * pay, h: b[3] - b[1] + 2 * pay };
    this.ciz(false);
  }

  /* piksel başına birim (metin/ok boyutları ekranda sabit kalsın) */
  ppu() {
    const r = this.kap.getBoundingClientRect();
    const w = r.width || 600, h = r.height || 400;
    return Math.min(w / this.v.w, h / this.v.h) || 1;
  }

  /* -- çizim ---------------------------------------------------------- */
  ciz(tamamen = true) {
    if (!this.kap) return;
    if (!this.gorunur()) { this.kirli = true; return; }
    this.kirli = false;
    const p = this.p;
    if (!p) {
      this.kap.innerHTML = `<div class="bos-mesaj">Dosya seçin; takım yolu, vektör sırası
        ve kenar boşlukları burada görünür.</div>`;
      this.svg = null;
      if (this.bilgi) this.bilgi.innerHTML = "";
      return;
    }
    const K = OzAyar.hepsi();
    const anahtar = p.ad + "|" + p.bloklar.length;
    if (!this.v || this.programAnahtar !== anahtar) {
      this.programAnahtar = anahtar;
      // Varsayılan: YOLA sığdır (tezgâhın tamamı yolu küçültür). Kenar boşluğu
      // çizgileri görünür alana kırpılır; tezgâhın tamamı için çift tık.
      const b = this.sinirlar().yol;
      const pay = Math.max(b[2] - b[0], b[3] - b[1]) * 0.16 + 2;
      this.v = { x0: b[0] - pay, y0: -(b[3] + pay),
                 w: b[2] - b[0] + 2 * pay, h: b[3] - b[1] + 2 * pay };
      this.sigdirMod = "yol";
      tamamen = true;
    }
    if (tamamen || !this.svg || !this.svg.isConnected) {
      this.kap.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg"
          preserveAspectRatio="xMidYMid meet">
          <g class="oz-izgara-k"></g><g class="oz-sinir-k"></g>
          <g class="oz-yol-k" transform="scale(1,-1)"></g>
          <g class="oz-ust-k"></g><g class="oz-canli-k"></g></svg>`;
      this.svg = this.kap.firstElementChild;
      this.baglaEtkilesim();
      this.yolCiz(K);
    }
    this.gorunumUygula(K);
    this.bilgiCiz(K);
  }

  yolCiz(K) {
    const p = this.p;
    const secili = p.secili_blok;
    let ic = "";
    if (K.kesim) {
      ic += p.bloklar.map(b => {
        const sinif = b.id === secili ? "secili" : (b.id > secili ? "aktif" : "");
        return `<path class="yol-kesim ${sinif}" d="${b.komut}"/>`;
      }).join("");
    }
    if (K.tasima) {
      for (let i = 1; i < p.bloklar.length; i++) {
        const o = p.bloklar[i - 1], y = p.bloklar[i];
        const ox = o.son ? o.son[0] : o.x, oy = o.son ? o.son[1] : o.y;
        ic += `<path class="yol-tasima" d="M${ox} ${oy} L${y.x} ${y.y}"/>`;
      }
    }
    ozQ(".oz-yol-k", this.svg).innerHTML = ic;
  }

  /* Zoom/pan sonrası yalnızca bunlar yeniden çizilir (yol dokunulmaz). */
  gorunumUygula(K) {
    const v = this.v, svg = this.svg;
    svg.setAttribute("viewBox", `${v.x0} ${v.y0} ${v.w} ${v.h}`);
    const u = 1 / this.ppu();               // 1 piksel = u birim
    ozQ(".oz-izgara-k", svg).innerHTML = K.izgara ? this.izgara(u) : "";
    ozQ(".oz-sinir-k", svg).innerHTML = K.sinir ? this.tezgah(u) : "";
    ozQ(".oz-ust-k", svg).innerHTML = this.ust(K, u);
    this.konumGuncelle();
  }

  izgara(u) {
    const v = this.v;
    const hedef = v.w / 8;
    const us = Math.pow(10, Math.floor(Math.log10(hedef)));
    const adim = [1, 2, 5, 10].map(m => m * us).find(a => a >= hedef) || us;
    const x0 = v.x0, x1 = v.x0 + v.w, yA = -(v.y0 + v.h), yB = -v.y0;
    let s = "";
    let n = 0;
    for (let x = Math.ceil(x0 / adim) * adim; x <= x1 && n < 80; x += adim, n++) {
      s += `<line class="oz-izgara" x1="${x}" y1="${v.y0}" x2="${x}" y2="${v.y0 + v.h}"/>`;
      s += `<text class="oz-izgara-yazi" x="${x + 3 * u}" y="${v.y0 + v.h - 4 * u}"
             font-size="${10 * u}">${+x.toFixed(6)}</text>`;
    }
    n = 0;
    for (let y = Math.ceil(yA / adim) * adim; y <= yB && n < 80; y += adim, n++) {
      s += `<line class="oz-izgara" x1="${x0}" y1="${-y}" x2="${x1}" y2="${-y}"/>`;
      s += `<text class="oz-izgara-yazi" x="${x0 + 3 * u}" y="${-y - 3 * u}"
             font-size="${10 * u}">${+y.toFixed(6)}</text>`;
    }
    return s;
  }

  tezgah(u) {
    const A = this.sinir && this.sinir.aralik;
    if (!A || !A.X || !A.Y) return "";
    const dogr = this.sinir.dogrulandi ? "" : " (doğrulanmadı)";
    return `<rect class="oz-sinir" x="${A.X[0]}" y="${-A.Y[1]}"
        width="${A.X[1] - A.X[0]}" height="${A.Y[1] - A.Y[0]}"/>
      <text class="oz-yazi oz-sinir-yazi" x="${A.X[0] + 6 * u}" y="${-A.Y[1] + 14 * u}"
        font-size="${11 * u}">tezgâh sınırı${dogr}</text>`;
  }

  /* vektör numaraları, geçişler, kenar boşlukları, ölçüler, sıfır */
  ust(K, u) {
    const p = this.p;
    const vek = (p.vektor && p.vektor.vektorler) || [];
    const secili = p.secili_blok;
    let s = "";

    // ---- kesim alanı kutusu + ölçü
    const o = p.olcu || {};
    const kes = o.kesim, tum = o.tum;
    if (K.olcu && kes) {
      s += `<rect class="olcu-kutu" x="${kes.x[0]}" y="${-kes.y[1]}"
          width="${Math.max(kes.genislik, 0.001)}" height="${Math.max(kes.yukseklik, 0.001)}"/>
        <text class="oz-yazi" text-anchor="middle" x="${(kes.x[0] + kes.x[1]) / 2}"
          y="${-kes.y[1] - 6 * u}" font-size="${11 * u}">${kes.genislik.toFixed(1)}</text>
        <text class="oz-yazi" x="${kes.x[1] + 6 * u}" y="${-(kes.y[0] + kes.y[1]) / 2}"
          font-size="${11 * u}">${kes.yukseklik.toFixed(1)}</text>`;
    }

    // ---- Z geçişleri: vektör sonu -> sonraki vektör başı (güvenli Z'de)
    if (K.gecis && vek.length > 1) {
      for (let i = 0; i + 1 < vek.length; i++) {
        const a = vek[i].son, b = vek[i + 1].bas;
        const dx = b[0] - a[0], dy = b[1] - a[1];
        const uz = Math.hypot(dx, dy);
        if (uz < 1e-6) continue;
        s += `<line class="oz-gecis" x1="${a[0]}" y1="${-a[1]}" x2="${b[0]}" y2="${-b[1]}"/>`;
        // yön oku (çizginin %60'ında, ekranda sabit boyut)
        const m = [a[0] + dx * 0.6, a[1] + dy * 0.6];
        const ux = dx / uz, uy = dy / uz, L = 9 * u, W = 4.5 * u;
        s += `<path class="oz-ok" d="M${m[0] + ux * L} ${-(m[1] + uy * L)}
          L${m[0] - uy * W} ${-(m[1] + ux * W)} L${m[0] + uy * W} ${-(m[1] - ux * W)}Z"/>`;
      }
    }

    // ---- etiketler (örtüşenleri ele: yakındaki numara çıkmaz)
    const aralik = 24 * u;
    const yerlesen = [];
    const yer = (x, y) => {
      for (const q of yerlesen)
        if (Math.abs(q[0] - x) < aralik && Math.abs(q[1] - y) < aralik * 0.7) return false;
      yerlesen.push([x, y]);
      return true;
    };
    if (K.blok) {
      p.bloklar.forEach(b => {
        const sinif = b.id === secili ? "secili" : (b.id < secili ? "onceki" : "");
        s += `<circle class="yol-nokta ${sinif}" cx="${b.x}" cy="${-b.y}" r="${3 * u}"/>`;
        if (yer(b.x, b.y))
          s += `<text class="yol-no oz-yazi ${sinif}" x="${b.x - 4 * u}" y="${-b.y + 12 * u}"
            text-anchor="end" font-size="${11 * u}">${b.no}</text>`;
      });
    }
    if (K.vektor) {
      vek.forEach(v => {
        const onceki = v.blok != null && v.blok < secili;
        const sinif = onceki ? "onceki" : (v.blok === secili ? "secili" : "");
        s += `<circle class="oz-vnokta ${sinif}" cx="${v.bas[0]}" cy="${-v.bas[1]}" r="${4 * u}"/>`;
        if (yer(v.bas[0], v.bas[1]))
          s += `<text class="oz-vno oz-yazi ${sinif}" x="${v.bas[0] + 6 * u}"
            y="${-v.bas[1] - 6 * u}" font-size="${12 * u}">${v.no}</text>`;
      });
    }
    if (K.vuzun) {
      const yer2 = [];
      vek.forEach(v => {
        const x = v.bas[0], y = v.bas[1];
        if (yer2.some(q => Math.abs(q[0] - x) < aralik * 2 && Math.abs(q[1] - y) < aralik * 0.7)) return;
        yer2.push([x, y]);
        s += `<text class="oz-yazi oz-vuzun" x="${x + 6 * u}" y="${-y + 14 * u}"
          font-size="${10 * u}">${v.uzunluk.toFixed(0)} mm</text>`;
      });
    }
    if (K.yon) {
      vek.forEach(v => {
        if (!v.yon) return;
        const ux = v.yon[0], uy = v.yon[1], L = 12 * u, W = 5 * u;
        const bx = v.bas[0] + ux * 6 * u, by = v.bas[1] + uy * 6 * u;
        s += `<path class="oz-yon" d="M${bx + ux * L} ${-(by + uy * L)}
          L${bx - uy * W} ${-(by + ux * W)} L${bx + uy * W} ${-(by - ux * W)}Z"/>`;
      });
    }

    // ---- kenar boşlukları: yolun her kenarından tezgâh sınırına kalan mesafe
    if (K.kenar) s += this.kenarlar(K, u);

    // ---- iş sıfırı
    if (K.sifir) {
      const b = 8 * u;
      s += `<path class="olcu-sifir" d="M${-b} 0 H${b} M0 ${-b} V${b}"/>
        <text class="oz-yazi sifir" x="${5 * u}" y="${15 * u}" font-size="${11 * u}">X0 Y0</text>`;
    }
    return s;
  }

  /* Kenar boşluğu hesabı (ayrı: bilgi şeridi de kullanır) */
  kenarHesap(K) {
    const o = this.p && this.p.olcu;
    const kutu = o && (K.zarf ? o.tum : o.kesim);
    const A = this.sinir && this.sinir.aralik;
    if (!kutu || !A || !A.X || !A.Y) return null;
    return {
      kutu,
      sol: { d: kutu.x[0] - A.X[0], a: [kutu.x[0], (kutu.y[0] + kutu.y[1]) / 2],
             b: [A.X[0], (kutu.y[0] + kutu.y[1]) / 2], ad: "sol" },
      sag: { d: A.X[1] - kutu.x[1], a: [kutu.x[1], (kutu.y[0] + kutu.y[1]) / 2],
             b: [A.X[1], (kutu.y[0] + kutu.y[1]) / 2], ad: "sağ" },
      alt: { d: kutu.y[0] - A.Y[0], a: [(kutu.x[0] + kutu.x[1]) / 2, kutu.y[0]],
             b: [(kutu.x[0] + kutu.x[1]) / 2, A.Y[0]], ad: "alt" },
      ust: { d: A.Y[1] - kutu.y[1], a: [(kutu.x[0] + kutu.x[1]) / 2, kutu.y[1]],
             b: [(kutu.x[0] + kutu.x[1]) / 2, A.Y[1]], ad: "üst" },
    };
  }

  kenarlar(K, u) {
    const h = this.kenarHesap(K);
    if (!h) {
      const v = this.v;
      return `<text class="oz-yazi oz-uyari" x="${v.x0 + 8 * u}" y="${v.y0 + 18 * u}"
        font-size="${11 * u}">Kenar boşlukları: tezgâh referansı / limit yok</text>`;
    }
    const k = h.kutu;
    const v = this.v, m = 10 * u;
    // Çizgi tezgâh sınırına kadar uzar ve görünür alanın dışına taşabilir;
    // bu yüzden çizgi ve ETİKET görünür alana KIRPILIR (etiket hep okunur).
    const r = { x0: v.x0 + m, y0: v.y0 + m, x1: v.x0 + v.w - m, y1: v.y0 + v.h - m };
    const icinde = (q) => q[0] >= r.x0 && q[0] <= r.x1 && q[1] >= r.y0 && q[1] <= r.y1;
    let s = `<rect class="oz-kutu" x="${k.x[0]}" y="${-k.y[1]}" width="${Math.max(k.genislik, 0.001)}"
      height="${Math.max(k.yukseklik, 0.001)}"/>`;
    ["sol", "sag", "alt", "ust"].forEach(t => {
      const e = h[t];
      const asim = e.d < 0;
      const cls = asim ? "oz-kenar asim" : (e.d < 20 ? "oz-kenar yakin" : "oz-kenar");
      const A = [e.a[0], -e.a[1]], B = [e.b[0], -e.b[1]];
      const seg = ozKirp(A, B, r);
      if (!seg) return;
      s += `<line class="${cls}" x1="${seg[0][0]}" y1="${seg[0][1]}" x2="${seg[1][0]}" y2="${seg[1][1]}"/>`;
      // uç çentikleri (sabit piksel): yalnızca görünen uçlarda
      const dik = (t === "sol" || t === "sag") ? [0, 1] : [1, 0];
      [A, B].forEach(q => {
        if (!icinde(q)) return;
        s += `<line class="${cls}" x1="${q[0] - dik[0] * 5 * u}" y1="${q[1] - dik[1] * 5 * u}"
          x2="${q[0] + dik[0] * 5 * u}" y2="${q[1] + dik[1] * 5 * u}"/>`;
      });
      const mx = (seg[0][0] + seg[1][0]) / 2, my = (seg[0][1] + seg[1][1]) / 2;
      const yatay = (t === "sol" || t === "sag");
      const metin = (e.d < 0 ? "AŞIYOR " : "") + e.d.toFixed(1) + " mm";
      s += `<text class="oz-yazi oz-kenar-yazi ${asim ? "asim" : e.d < 20 ? "yakin" : ""}"
        x="${mx + (yatay ? 0 : 7 * u)}" y="${my + (yatay ? -6 * u : 4 * u)}"
        text-anchor="${yatay ? "middle" : "start"}"
        font-size="${12 * u}">${metin}</text>`;
    });
    return s;
  }

  /* kesim alanı ölçüleri + kenar boşlukları şeridi */
  bilgiCiz(K) {
    if (!this.bilgi) return;
    const p = this.p;
    const o = p && p.olcu;
    if (!o || !o.kesim) { this.bilgi.innerHTML = ""; return; }
    const kutu = (v) => v == null ? "–" : v.toFixed(1);
    const oge = (ad, deger, ipucu = "", sinif = "") =>
      `<span class="olcu-oge ${sinif}" title="${ozKac(ipucu)}"><i>${ad}</i><b>${deger}</b></span>`;
    const k = o.kesim, t = o.tum, s = o.sifira_gore || {}, z = o.z || {};
    let h = oge("kesim alanı", `${kutu(k.genislik)} × ${kutu(k.yukseklik)} mm`,
        "Yalnızca kesim bloklarının kapladığı alan (yaylar dahil)") +
      oge("X", `${kutu(k.x[0])} … ${kutu(k.x[1])}`, "İş koordinatında X aralığı") +
      oge("Y", `${kutu(k.y[0])} … ${kutu(k.y[1])}`, "İş koordinatında Y aralığı") +
      oge("Z", `${kutu(z.min)} … ${kutu(z.max)}`, "En derin / en yüksek Z") +
      oge("hareket zarfı", `${kutu(t.genislik)} × ${kutu(t.yukseklik)} mm`,
          "Boşta (G0) gidişler dahil tüm hareket alanı — yumuşak limitleri aşan genelde budur");
    if (s.kosegen != null) h += oge("köşegen", `${kutu(s.kosegen)} mm`, "Kesim alanının köşegeni");
    const vk = p.vektor;
    if (vk && vk.vektorler && vk.vektorler.length) {
      const top = vk.vektorler.reduce((a, v) => a + v.uzunluk, 0);
      h += oge("vektör", `${vk.vektorler.length} adet · ${(top / 1000).toFixed(2)} m`,
        `Güvenli Z (${vk.guvenli_z}) yükselişleriyle ayrılan kesim parçaları (kaynak: ${vk.kaynak})`);
    } else if (vk && vk.uyari) {
      h += oge("vektör", "yok", vk.uyari, "uyari");
    }
    const kh = this.kenarHesap(K);
    if (kh) {
      ["sol", "sag", "alt", "ust"].forEach(t => {
        const e = kh[t];
        h += oge("boşluk " + e.ad, `${e.d.toFixed(1)} mm`,
          `${K.zarf ? "G0 zarfı" : "Kesim alanı"} ile tezgâh sınırı arasında kalan mesafe`,
          e.d < 0 ? "asim" : (e.d < 20 ? "yakin" : ""));
      });
    }
    this.bilgi.innerHTML = h;
  }

  /* canlı takım konumu: yeniden çizim gerektirmez */
  konumGuncelle() {
    if (!this.svg) return;
    const k = ozQ(".oz-canli-k", this.svg);
    if (!k) return;
    if (!OzAyar.al("konum") || !this.konum) { k.innerHTML = ""; return; }
    const u = 1 / this.ppu();
    const r = 7 * u;
    const x = this.konum.X, y = -this.konum.Y;
    k.innerHTML = `<circle class="canli-halka" cx="${x}" cy="${y}" r="${r}"/>
      <path class="canli-arti" d="M${x - 2 * r} ${y}H${x + 2 * r}M${x} ${y - 2 * r}V${y + 2 * r}"/>`;
  }

  /* -- etkileşim: tekerlek = yakınlaştır, sürükle = kaydır, çift tık = sığdır */
  baglaEtkilesim() {
    const svg = this.svg;
    svg.addEventListener("wheel", (e) => {
      e.preventDefault();
      const k = e.deltaY > 0 ? 1.15 : 1 / 1.15;
      const ctm = svg.getScreenCTM();
      if (!ctm) return;
      const pt = new DOMPoint(e.clientX, e.clientY).matrixTransform(ctm.inverse());
      const v = this.v;
      v.x0 = pt.x - (pt.x - v.x0) * k;
      v.y0 = pt.y - (pt.y - v.y0) * k;
      v.w *= k; v.h *= k;
      this.gorunumUygula(OzAyar.hepsi());
    }, { passive: false });
    svg.addEventListener("pointerdown", (e) => {
      this.surukle = { x: e.clientX, y: e.clientY };
      try { svg.setPointerCapture(e.pointerId); } catch (x) { /* eski tarayıcı */ }
    });
    svg.addEventListener("pointermove", (e) => {
      if (!this.surukle) return;
      const ctm = svg.getScreenCTM();
      if (!ctm) return;
      this.v.x0 -= (e.clientX - this.surukle.x) / ctm.a;
      this.v.y0 -= (e.clientY - this.surukle.y) / ctm.d;
      this.surukle = { x: e.clientX, y: e.clientY };
      this.gorunumUygula(OzAyar.hepsi());
    });
    const birak = () => { this.surukle = null; };
    svg.addEventListener("pointerup", birak);
    svg.addEventListener("pointercancel", birak);
    svg.addEventListener("dblclick", () => {
      // yol <-> tezgâhın tamamı (tezgâh sınırı varsa)
      const A = this.sinir && this.sinir.aralik;
      const tumVar = !!(A && A.X && A.Y);
      this.sigdir(this.sigdirMod === "yol" && tumVar ? "tum" : "yol");
    });
  }
}

/* ================================================================== */
/* Araç çubuğu + katman çipleri                                        */
/* ================================================================== */
function ozGorunurYenile() {
  OZ_KAYIT.forEach(o => { if (o.kirli || o.gorunur()) o.ciz(); });
}
function onizlemeCiz() { ozGorunurYenile(); }       // eski ad
function ozProgram(p) { OZ_KAYIT.forEach(o => o.program(p)); }
function ozDurum(d) { OZ_KAYIT.forEach(o => o.durum(d)); }

function ozBul(kapSecici) {
  return OZ_KAYIT.find(o => o.kap === ozQ(kapSecici));
}

/* Çip çubuğu: her katman tek tıkla açılır/kapanır. */
function ozCipleriKur(kap) {
  const ciz = () => {
    const K = OzAyar.hepsi();
    kap.innerHTML = OZ_KATMANLAR.map(l =>
      `<button type="button" class="oz-chip${K[l.id] ? " aktif" : ""}" data-k="${l.id}"
        aria-pressed="${K[l.id]}" title="${ozKac(l.ip || l.ad)}">${l.ad}</button>`).join("") +
      `<span class="oz-chip-ek"><button type="button" class="oz-chip-link" data-t="hepsi">hepsi</button>
        <button type="button" class="oz-chip-link" data-t="hicbiri">hiçbiri</button>
        <button type="button" class="oz-chip-link" data-t="varsayilan">varsayılan</button></span>`;
  };
  kap.addEventListener("click", (e) => {
    const c = e.target.closest(".oz-chip");
    if (c) { OzAyar.ayarla(c.dataset.k, !OzAyar.al(c.dataset.k)); return; }
    const t = e.target.closest(".oz-chip-link");
    if (!t) return;
    if (t.dataset.t === "hepsi") OzAyar.topluAyarla(true);
    else if (t.dataset.t === "hicbiri") OzAyar.topluAyarla(false);
    else OzAyar.varsayilan();
  });
  OzAyar.dinle(ciz);
  ciz();
}

/* Gerçek tam ekran (Fullscreen API). Tarayıcı reddederse (izin yok, gömülü
   görünüm, iframe...) öğe PENCEREYİ KAPLAYAN "sahte tam ekran"a geçer; böylece
   düğme her ortamda çalışır. */
function ozSahteKapat() {
  ozQQ(".oz-sahte-tam").forEach(e => e.classList.remove("oz-sahte-tam"));
  setTimeout(ozGorunurYenile, 60);
}
function ozTamEkran(elem) {
  if (!elem) return;
  if (document.fullscreenElement) { document.exitFullscreen(); return; }
  if (elem.classList.contains("oz-sahte-tam")) { ozSahteKapat(); return; }
  const dene = elem.requestFullscreen ? elem.requestFullscreen() : Promise.reject();
  Promise.resolve(dene).catch(() => {
    elem.classList.add("oz-sahte-tam");
    setTimeout(ozGorunurYenile, 60);
  });
}

/* Ayrı pencerede aç ve ekranı kaplat (gerçek tam ekran için pencerede F). */
function ozPencereAc() {
  const w = window.open("/onizleme.html", "m3_onizleme",
                        "popup=yes,width=1280,height=820");
  if (!w) {
    if (typeof bildir === "function")
      bildir("Açılır pencere engellendi — tarayıcıdan bu site için izin verin", "uyari");
    return null;
  }
  try {
    w.moveTo(screen.availLeft || 0, screen.availTop || 0);
    w.resizeTo(screen.availWidth, screen.availHeight);
  } catch (e) { /* bazı tarayıcılar engeller */ }
  w.focus();
  return w;
}

function ozAraclariKur() {
  ozQQ(".oz-arac").forEach((kap) => {
    const hedef = kap.dataset.oz;               // çizici kapsayıcısı
    const tam = kap.dataset.tam;                // tam ekran yapılacak öğe
    const sinif = kap.dataset.btn || "mini";
    const cipKap = ozQ(kap.dataset.cip);
    kap.innerHTML = `
      <button type="button" class="${sinif}" data-i="katman" title="Gösterilecek katmanlar">Katmanlar</button>
      <button type="button" class="${sinif}" data-i="sigdir" title="Yola sığdır (çift tık: tümü)">⤢</button>
      <button type="button" class="${sinif}" data-i="tam" title="Tam ekran">⛶</button>
      ${kap.dataset.pencere === "0" ? "" :
        `<button type="button" class="${sinif}" data-i="pencere" title="Ayrı pencerede aç">↗ Pencere</button>`}`;
    kap.addEventListener("click", (e) => {
      const b = e.target.closest("[data-i]");
      if (!b) return;
      const oz = ozBul(hedef);
      if (b.dataset.i === "katman" && cipKap) {
        const gizli = cipKap.classList.toggle("gizli");
        try { localStorage.setItem("m3_oz_cip_" + hedef, gizli ? "0" : "1"); } catch (x) { /* */ }
      } else if (b.dataset.i === "sigdir" && oz) oz.sigdir("yol");
      else if (b.dataset.i === "tam") ozTamEkran(ozQ(tam));
      else if (b.dataset.i === "pencere") ozPencereAc();
    });
    if (cipKap) {
      ozCipleriKur(cipKap);
      let goster = "1";
      try { goster = localStorage.getItem("m3_oz_cip_" + hedef) || "1"; } catch (x) { /* */ }
      cipKap.classList.toggle("gizli", goster === "0");
    }
  });
}

// tam ekran / boyut değişince yeniden çiz (SVG ölçüleri değişir)
document.addEventListener("fullscreenchange", () => setTimeout(ozGorunurYenile, 60));
window.addEventListener("resize", () => {
  clearTimeout(window.__ozResizeT);
  window.__ozResizeT = setTimeout(ozGorunurYenile, 120);
});
