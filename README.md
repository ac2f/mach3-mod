# Mach3-Kontrol

Mach3'un **yerine gecmeyen**, **yaninda calisan** bir kontrol arayuzu.
Mach3 makineyi surmeye devam eder; bu program ona baglanir, komutlari
**gondermeden once guvenlik denetiminden gecirir** ve isi kolaylastiran bir
arayuz sunar:

* **Kaza engelleyici guvenlik katmani** — yumusak limitler, adim/besleme
  sinirlari, dalis derinligi siniri, guvenli Z, program oncesi kapsam
  denetimi, olu adam butonu (kalp atisi), tek-operator kilidi, olay gunlugu.
* **Klavye kilidi** — jog sirasinda yanlislikla **Windows / Alt / Tab**
  tusuna basmak Mach3'un odagini kaybettirir ve eksen, odak geri gelene kadar
  **durmaz**. Kilit acikken jog tuslari disindaki **tum tuslar yutulur**;
  buna ragmen odak giderse hareket otomatik durdurulur.
* **Web arayuzu** — ayni ag uzerindeki **telefondan/tabletten** kontrol.
  Ek kutuphane yok (Python standart kutuphanesi).
* **Kesim sirasi gorunumu** — [Cnc-Assistant](https://github.com/ac2f/Cnc-Assistant)
  ile ayni blok numaralandirmasi; **hangi vektorden baslayacaginizi secip**
  oradan guvenle baslatabilirsiniz.
* **Hassas hareket** — "X ekseninde sola 25 mm git" tek tikla.
* **Kaydirmali sifirlama** — "su anki konumun **5 mm sagi** yeni X sifiri
  olsun" (ayni sekilde Y ve Z icin).
* **Kesim suresi tahmini** — ivme ve kose kayiplari hesaba katilarak,
  secili bloktan sonuna kadar.

---

## ⚠️ Once guvenlik

Bu program **bir guvenlik cihazi degildir**.

* **Donanim acil-stop butonunuzun ve limit switchlerinizin yerine gecmez.**
  Yazilim cokebilir, Windows donabilir, ag kopabilir. Makinenin fisini
  cekecek fiziksel bir buton her zaman elinizin altinda olmalidir.
* **Ctrl+Alt+Del engellenemez.** Bu kombinasyonu Windows guvenli masaustune
  ayirir; hicbir uygulama yakalayamaz. Basilirsa odak gider — bu durumda
  ikinci katman (odak bekcisi) devreye girip hareketi durdurur.
* Yumusak limitler **siz girene kadar** varsayilan (ornek) degerlerdedir.
  Arayuz bunu turuncu seritle surekli hatirlatir; **Guvenlik** sekmesinden
  makinenizin gercek olculerini girip "dogruladim" kutusunu isaretleyin.
* Ilk denemeyi **malzeme ve takim olmadan**, tercihen `--simulasyon` modunda
  yapin.

---

## Kurulum

Makinenin (Mach3'un calistigi) Windows bilgisayarina:

```bash
# Python 3.8+ gerekir. Cekirdek icin EK KUTUPHANE YOK.
# Yalnizca gercek Mach3 baglantisi icin:
pip install pywin32

# veya:
pip install -r requirements.txt
```

Baslatma:

```bash
python main.py                    # web arayuzu (varsayilan port 8010)
python main.py --port 8010
python main.py --simulasyon       # Mach3 olmadan dene (sanal tezgah)
python main.py --tarayici-ac
```

Konsolda su yazar:

```
  Bu bilgisayardan : http://127.0.0.1:8010
  Telefondan       : http://192.168.1.34:8010
  PIN              : 481902
```

Telefondan o adrese girip **PIN**'i yazarsiniz. (PIN
`~/.mach3_kontrol/ayarlar.json` icindedir, degistirilebilir.)

> Sunucuyu **internete acmayin**. Ev/atolye agi icin tasarlanmistir:
> yerel bilgisayardan gelen istekler dogrudan, agdaki diger cihazlar PIN ile
> kabul edilir.

---

## Mach3 ile baglanti (onemli)

Baglanti, Mach3'un **OLE/COM otomasyon arayuzu** uzerinden kurulur
(`Mach4.Document` — evet, Mach3'te bile ProgID boyledir; farkli ise Ayarlar >
Baglanti'dan degistirin). Bu arayuz `GetOEMDRO / SetOEMDRO / Code /
DoOEMButton` gibi metotlar sunar.

**Iki nokta kendi makinenizde dogrulanmalidir:**

1. **OEM kodlari.** Mach3 surumune ve profile gore degisebilir. Ayarlar >
   *Mach3 OEM kodlari* panelinde hepsi listelenir; her kodun yaninda
   `guven: yuksek | orta | dusuk` etiketi vardir. **`dusuk` olanlari** Mach3
   icinde (Operator → OEM kod listesi) kontrol edip duzeltin. Degistirdiginiz
   kodlar ayar dosyasina yazilir.
2. **Metot destegi.** Bazi surumlerde `LoadFile` gibi metotlar bulunmaz.
   Program baglanirken hangi metotlarin var oldugunu yoklar ve Ayarlar
   sayfasinda yesil/kirmizi rozetlerle gosterir; eksik metot sessizce
   yutulmaz, islem yapilmak istendiginde acik hata verir.

`LoadFile` yoksa dosyayi Mach3'te elle acar, arayuzden yalnizca
baslat/duraklat/durdur ve jog kullanirsiniz.

---

## Ne yapar? (detay)

### 1) Guvenlik katmani

Makineye giden **her** hareket komutu once denetlenir; gecemezse
**gonderilmez** ve nedeni ekranda listelenir.

| Denetim | Aciklama |
|---|---|
| Yumusak limit | Hedef makine koordinati calisma hacminin (pay dahil) icinde mi |
| Jog adimi | Tek adim `max_jog_adim` degerini asamaz |
| Besleme | Jog beslemesi `max_jog_feed` degerini asamaz |
| Dalis derinligi | Elle Z inisi `max_dalis_derinlik` altina inemez |
| Guvenli Z | XY tasima oncesi takim guvenli yuksekli§e cikarilir |
| Program kapsami | Dosyanin **tum** hareketleri (yaylarin gercek uc noktalari dahil) mevcut is sifirina gore hacme sigiyor mu |
| Kalp atisi | Surekli jog, istemciden ~400 ms'de bir "hala basiliyim" almazsa durur |
| Sure siniri | Surekli jog en fazla `max_surekli_jog_sn` surer |
| Kontrol kilidi | Ayni anda yalnizca **tek** cihaz komut verebilir |
| Olay gunlugu | Gonderilen her komut zaman damgasiyla `olaylar.log` dosyasina yazilir |

**Acil durdur / feed hold** hicbir denetime tabi degildir: kontrol kimde
olursa olsun, her cihazdan, her an calisir (`Esc` tusu da acil durdurur).

### 2) Klavye kilidi ve odak bekcisi

Problem: Mach3'te ok tuslariyla jog yaparken Windows tusuna basarsaniz
Mach3 odagi kaybeder, tusu biraktiginiz bilgisi **Mach3'e ulasmaz** ve eksen
odak geri gelene kadar hareketine devam eder.

Cozum iki katmanlidir:

1. **Dusuk seviye klavye hooku** (`SetWindowsHookEx(WH_KEYBOARD_LL)`) —
   kilit acikken tum klavye, isletim sistemine ulasmadan yakalanir. Yalnizca
   izinli tuslar (ok tuslari, PgUp/PgDn, Space, Esc) gecer; **Windows, Alt,
   Tab, Ctrl+Esc ve digerleri yutulur.** Izin listesi ayarlanabilir.
2. **Odak bekcisi** — buna ragmen Mach3 odagi kaybederse (~100 ms icinde)
   fark edilir, **feed hold + stop** gonderilir ve istenirse odak Mach3'e
   geri verilir.

Tarayici tarafinda da ayni kilit vardir (her platformda calisir): kilit
acikken sayfadaki tum tus olaylari yutulur, yalnizca jog tuslari isler.
Ayrica sekme gizlenir/pencere odagi giderse **jog aninda durur**.

Kilit acikken klavye:

| Tus | Islev |
|---|---|
| `←` `→` | X ekseni − / + |
| `↑` `↓` | Y ekseni + / − |
| `PgUp` `PgDn` | Z ekseni + / − |
| `Space` | Feed hold |
| `Esc` | ACIL DURDUR |

> Windows hooku yalnizca Windows'ta calisir ve Mach3 yonetici olarak
> calisiyorsa bu programin da yonetici olarak calismasi gerekebilir.

### 3) Kesim sirasi ve **bloktan baslatma**

Dosya, Cnc-Assistant ile **ayni kurala** gore bloklara ayrilir: *bir kesim
blogu, X ve Y iceren bir G0 hizli hareketiyle baslar.* Boylece
Cnc-Assistant'in `*_reordered.tap` ciktisindaki numaralar burada da aynidir.

Arayuzde:

* Numarali **sira listesi** (baslangic X/Y, kesim uzunlugu, satir sayisi,
  o bloktan sonuna kalan sure).
* **Takim yolu onizlemesi** — yaylar gercek egri olarak cizilir; secili blok
  yesil, sonraki bloklar mavi, bitmis bloklar soluk. Tekerlekle yakinlastirma,
  surukleyerek kaydirma, cift tikla sifirlama.
* Bir bloga tiklayip **oradan baslatirsiniz**.

Bloktan baslatma **guvenli yeniden baslatma dosyasi** uretir (orijinal dosya
degistirilmez):

```
1. G21/G20 · G90 · G17 · G40        (birim ve modlar geri kurulur)
2. G0 Z<guvenli>                    (ONCE guvenli yuksekli§e cikilir)
3. T.. M6 · S.. · M3 · G4 P4 · M8   (takim, devir, spindle, sogutma)
4. G0 X.. Y..                       (hedef blogun basina HAVADAN gidilir)
5. <secilen bloklar aynen>          (satirlar hic degistirilmez)
6. <orijinal program sonu>          (M5 / M9 / M30)
```

G91 (artimli) iceren dosyalarda bloktan baslatma **reddedilir** — konum
kaymasi riski vardir.

### 4) Hassas hareket ve kaydirmali sifirlama

* **Hassas hareket:** her eksen icin mesafe kutusu + yon dugmeleri
  ("X ekseninde sola 25 mm", "Z'de 0.5 mm yukari"). Mutlak konuma gitme de
  vardir ve gerekirse **once Z'yi guvenli yuksekli§e alir**.
* **Kaydirmali sifirlama:** ofseti yazip yonu secersiniz.
  *"Su anki konumun 5 mm sagi yeni X sifiri olsun"* → DRO **−5.000** gosterir
  (yani sifira 5 mm var). 5 mm saga gidince tam sifira gelirsiniz.
  Ayni mantik Y (ileri/geri) ve Z (yukari/asagi — kagit payi icin ideal)
  icin de gecerlidir. Ofset 0 ise klasik "burasi sifir".
  Yontem `SetOEMDRO` (varsayilan) veya `G10 L20` olabilir (Ayarlar > Makine).

### 5) Kesim suresi tahmini

Her hareket icin **yamuk (trapez) hiz profili** kullanilir:

```
d >= v²/a  ->  t = d/v + v/a          (besleme hizina ulasilir)
d <  v²/a  ->  t = 2·sqrt(d/a)        (ucgen profil)
```

`kose_kaybi` katsayisi (0..1) ardisik hareketlerin harmanlanmasini modeller.
G4 beklemeleri, takim degisimi (M6) ve spindle devire ulasma suresi eklenir.
Besleme override (FRO) kesim suresini dogrudan olcekler. Gercek sure ile
tahmin arasindaki oran `sure_katsayi` ile kalibre edilir (Ayarlar > Makine).

Terminalden de alinabilir:

```bash
python main.py --sure kesim.tap    # toplam + kesim/bosta/bekleme kirilimi
python main.py --sira kesim.tap    # blok blok liste (sure dahil)
```

---

## Arayuz

| Sekme | Icerik |
|---|---|
| **Kontrol** | DRO (is + makine), jog padi (adim / basili tut), hassas hareket, kaydirmali sifirlama, klavye kilidi, MDI |
| **Program & Sira** | Dosya secme, numarali kesim sirasi, takim yolu onizlemesi, sure tahmini, guvenlik denetimi, baslat/feed hold/durdur |
| **Guvenlik** | Yumusak limitler, hareket sinirlari, guncel guvenlik durumu |
| **Ayarlar** | Baglanti (COM/simulasyon, ProgID), makine (hiz/ivme/sifirlama yontemi), **OEM kod haritasi** |
| **Gunluk** | Gonderilen her komut, her guvenlik reddi, her acil durdurma |

Acik/koyu tema otomatik (sag ustteki ◐ ile degistirilebilir). Telefonda
sekmeler alt cubuga gecer, jog dugmeleri buyur.

**ACIL DURDUR** dugmesi her sayfada, ust barda sabittir.

---

## Simulasyon modu

```bash
python ornekler/ornek_uret.py     # ornek kesim dosyasi uretir
python main.py --simulasyon
```

Sanal tezgah G0/G1/G2/G3 hareketlerini gercek besleme hizinda ilerletir,
G90/G91 · G4 · M3/M5 · F/S degerlerini takip eder; feed hold, stop ve jog
durdurma gercek makinedeki gibi davranir. Arayuzu, guvenlik kurallarini ve
sure tahminini makineye hic dokunmadan denemek icin kullanin.

---

## Ayar dosyasi

`~/.mach3_kontrol/ayarlar.json` (ilk caliştirmada olusur). Onemli alanlar:

```jsonc
{
  "baglanti": { "tur": "otomatik", "progid": "Mach4.Document" },
  "makine":   { "hizli_hiz": {"X":8000,"Y":8000,"Z":3000},
                "ivme": {"X":400,"Y":400,"Z":250},
                "sifirlama_yontemi": "dro" },
  "guvenlik": {
    "limitler_dogrulandi": false,          // ← makinenizi girince true yapin
    "yumusak_limit": { "X": {"min":0,"max":3000},
                       "Y": {"min":0,"max":1500},
                       "Z": {"min":-150,"max":0} },
    "max_jog_adim": 100, "max_jog_feed": 6000,
    "guvenli_z": 20, "max_dalis_derinlik": -60,
    "max_surekli_jog_sn": 30, "jog_kalp_atisi_ms": 400,
    "pin": "……"
  },
  "klavye": { "kilit_acilista": false, "odak_bekcisi": true,
              "odak_pencere_baslik": "Mach3" }
}
```

Butun bu alanlar arayuzden de duzenlenebilir.

---

## Proje yapisi

```
mach3-mod/
├── main.py                        # giris noktasi
├── mach3_kontrol/
│   ├── ayarlar.py                 # JSON yapilandirma
│   ├── kodlar.py                  # OEM DRO/dugme/LED kod haritasi
│   ├── baglanti.py                # COM (Mach3) + simulasyon arka uclari
│   ├── guvenlik.py                # limitler, kalp atisi, kontrol kilidi, gunluk
│   ├── klavye_kilidi.py           # Windows klavye hooku + odak bekcisi
│   ├── gcode.py                   # blok ayristirma, kapsam, bloktan baslatma
│   ├── sure.py                    # kesim suresi tahmini
│   ├── denetleyici.py             # hepsini birlestiren beyin
│   ├── webapp.py                  # bagimliliksiz web sunucusu + API + SSE
│   └── web/                       # arayuz (index.html · app.css · app.js)
├── ornekler/ornek_uret.py
├── tests/
└── README.md
```

---

## Testler

```bash
pytest                       # veya tek tek:
python tests/test_gcode.py         # blok ayristirma, yay kapsami, bloktan baslatma
python tests/test_guvenlik.py      # limit reddi, kalp atisi, kontrol kilidi
python tests/test_sure.py          # sure modeli
python tests/test_denetleyici.py   # uctan uca (simulasyon): jog, sifirlama, baslatma
python tests/test_web.py           # HTTP API + olay akisi
```

---

## Sinirlar

* Gercek baglanti icin **Windows + Mach3 + pywin32** gerekir; digerlerinde
  yalnizca simulasyon calisir.
* Mach3'un COM arayuzunde guvenilir bir "surekli jog" cagrisi yoktur; basili
  tut modu, **kisa artimli adimlarin** zincirlenmesiyle uretilir. Her adim
  ayri ayri denetlenir ve kalp atisi kesilince zincir aninda durur. Bu,
  Mach3'un kendi jogundan biraz daha az akici ama daha guvenlidir.
* Program calisirken "kacinci bloktayim" bilgisi Mach3'ten okunmaz; ilerleme
  cubugu yerine **tahmini sure** gosterilir.

---

## Lisans

MIT — bkz. [LICENSE](LICENSE).
