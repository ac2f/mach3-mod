/* Ayrı önizleme penceresi: canlı veriyi sunucudan kendisi alır.
   - /olay (SSE): takım konumu, tezgâh sınırı, hangi program yüklü
   - /api/program/bilgi: program (bloklar, vektörler, ölçüler) — program ya da
     seçili blok değişince yeniden alınır
   Katman ayarları ana pencereyle localStorage üzerinden eşitlenir. */
"use strict";

(function () {
  const oz = new OzGorunum("#ozKap", "#ozBilgi");
  ozAraclariKur();

  const ad = ozQ("#ozAd"), durum = ozQ("#ozDurum");
  let programAnahtar = "\u0000";

  function post(uc, govde) {
    return fetch(uc, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(govde || {}),
    }).then(r => r.json());
  }

  async function programAl() {
    try {
      const p = await post("/api/program/bilgi", {});
      oz.program(p && p.bloklar ? p : null);
      ad.textContent = p && p.ad ? p.ad : "Önizleme";
      document.title = (p && p.ad ? p.ad + " — " : "") + "Takım yolu önizlemesi";
    } catch (e) { /* sonraki durum karesinde yeniden denenir */ programAnahtar = "\u0000"; }
  }

  const sse = new EventSource("/olay");
  sse.onmessage = (o) => {
    let d;
    try { d = JSON.parse(o.data); } catch (e) { return; }
    const p = d.program;
    const anahtar = p ? `${p.yol}|${p.secili_blok}` : "";
    if (anahtar !== programAnahtar) { programAnahtar = anahtar; programAl(); }
    oz.durum(d);
    const k = d.konum || {};
    const hm = (d.profiller && d.profiller.aktif_gercek) || "";
    durum.className = "oz-durum" + (d.bagli ? " ok" : " kotu");
    durum.textContent = (d.bagli ? "bağlı" : "bağlantı yok") +
      (k.X != null ? `  ·  X ${k.X.toFixed(3)}  Y ${(k.Y == null ? 0 : k.Y).toFixed(3)}` +
                     `  Z ${(k.Z == null ? 0 : k.Z).toFixed(3)}` : "") +
      (hm ? `  ·  HOME: ${hm}` : "") + (d.estop ? "  ·  RESET" : "");
  };
  sse.onerror = () => {
    durum.className = "oz-durum kotu";
    durum.textContent = "akış koptu — yeniden bağlanıyor…";
  };

  // Tam ekran yalnızca kullanıcı hareketiyle başlayabilir: F tuşu ya da ⛶ düğmesi
  document.addEventListener("keydown", (e) => {
    if (e.target && /INPUT|TEXTAREA|SELECT/.test(e.target.tagName)) return;
    if (e.key === "f" || e.key === "F") { e.preventDefault(); ozTamEkran(ozQ("#ozPencere")); }
    // bu pencerede Esc ACİL DURDUR değildir: sahte tam ekrandan çıkar
    if (e.key === "Escape") ozSahteKapat();
  });

  // Tam ekran ana pencerenin ACİL DURDUR'unu örter: burada da bir tane var
  ozQ("#ozAcil").onclick = () => {
    post("/api/acil", { sebep: "önizleme penceresi" })
      .then(() => { durum.textContent = "ACİL DURDUR gönderildi"; })
      .catch(() => { durum.textContent = "ACİL DURDUR gönderilemedi!"; });
  };
})();
