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

**Mach3 tarafina hicbir sey kurulmaz.** Eklenti (plugin), makro (`.m1s`),
brain, screenset veya XML degisikligi **gerekmez**; Mach3 klasorune tek bir
dosya bile yazilmaz. Kullanilan sey, Mach3'un kendi kurulumuyla birlikte
gelen **OLE/COM otomasyon arayuzudur** (`Mach4.Document` — evet, Mach3'te
bile ProgID boyledir; farkli ise Ayarlar > Baglanti'dan degistirin). Bu
arayuz `GetOEMDRO / SetOEMDRO / Code / DoOEMButton` gibi metotlar sunar.
Kurulan tek sey Windows'a **Python + pywin32**'dir.

### Mach3'u ELLE ACMAYIN — program kendisi acar

Bu, en cok zaman kaybettiren noktadir:

> **Mach3, COM otomasyon arayuzunu YALNIZCA `/Automation` anahtariyla
> baslatildiginda acar.** Cift tiklayarak (elle) acilan bir Mach3 kendini ne
> Running Object Table'a kaydeder ne de bir COM sinif fabrikasi olusturur —
> ona baglanmak **teknik olarak mumkun degildir**. Mach3 ekranda apacik
> dururken bile `Server execution failed` alirsiniz.

Bu yuzden program, Mach3'u **kendisi** dogru sekilde baslatir:

```
C:\Mach3\Mach3.exe /p Mach3Mill /Automation      (calisma dizini: C:\Mach3)
```

Uc ayrintinin ucu de sarttir:

| Ayrinti | Atlanirsa ne olur |
|---|---|
| `/Automation` | COM arayuzu hic acilmaz |
| Calisma dizini = Mach3 klasoru | Mach3 lisans dosyasini (`Mach1Lic.dat`) bulamaz, **"Corrupt install, no license file found"** deyip kapanir |
| `/p <profil>` | Mach3 profil secim penceresinde bekler, COM arayuzunu acmaz |

Yani **once program, sonra Mach3**: `python main.py` deyin, Mach3'u o acsin.
Mach3'u yine de elle acmak isterseniz kisayolunuza `/Automation` ekleyin.

**Her Mach3 acilisina yalnizca BIR istemci baglanabilir.** Mach3'un sinif
fabrikasi tek kullanimliktir (`REGCLS_SINGLEUSE`): ilk baglantidan sonra
iptal olur. Programi yeniden baslatirsaniz **Mach3'u de kapatip actirmaniz**
gerekir; aksi halde "Mach3 acik ama COM arayuzu kullanilamiyor" hatasini
alirsiniz.

Ilgili ayarlar (`Ayarlar > Baglanti`):

| Ayar | Varsayilan | Ne ise yarar |
|---|---|---|
| `mach3_baslat` | `true` | Mach3 kapaliysa program onu baslatir |
| `profil` | `Mach3Mill` | `/p` ile gecilen Mach3 profili |
| `mach3_exe` | *(bos)* | Bos ise kayit defterinden bulunur |
| `acilis_zaman_asimi_sn` | `120` | Mach3'un acilmasi icin beklenecek sure |

### Sifirlama Mach3 RESET durumundayken CALISMAZ

Gercek makinede olculdu: Mach3 **Reset (E-Stop)** durumundayken `SetOEMDRO`
cagrisini **sessizce yok sayar** — hata dondurmez, DRO da degismez. Bu yuzden
program her sifirlama sonrasi DRO'yu **geri okuyup dogrular**; yazma
gerceklesmediyse acik hata verir:

> `X sifirlanamadi: Mach3 RESET (E-Stop) durumunda. Mach3'te Reset dugmesine
> basip kirmizi yanip sonmeyi durdurun, sonra tekrar deneyin.`

Tezgah sekmesinde Mach3 bu haldeyken yanip sonen kirmizi **RESET** rozeti
gorunur. Ayni dogrulama profil yuklemede de yapilir.

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

### "Baglanti yok" diyorsa

```bash
python main.py --tani
```

Teshis, zinciri bastan sona dener ve nerede koptugunu gosterir: Python
32/64 bit, yonetici yetkisi, pywin32, **Mach3 gercekten calisiyor mu**
(pencere + surec + bit genisligi), **kayit defterindeki Mach ProgID'leri
(hem 32 hem 64 bit gorunumde)**, her ProgID icin baglanti denemesi ve son
olarak DRO okuma testi. En sik sebepler:

1. **Mach3 elle acilmis.** Yukaridaki bolume bakin: elle acilan Mach3'e
   baglanilamaz. Mach3'u tamamen kapatin, `python main.py` deyin. Ayni sekilde,
   bu Mach3 acilisina zaten bir istemci baglandiysa (fabrika tek kullanimlik)
   yine Mach3'u yeniden actirmaniz gerekir.
2. **32/64 bit uyusmazligi.** Mach3 **32-bit** bir programdir;
   COM kaydini kayit defterinin 32-bit gorunumune (`Wow6432Node`) yazar.
   **64-bit Python bu kaydi goremez** ve `Invalid class string` hatasi verir.
   Cozum: **32-bit Python kurun** (64-bit'in yaninda durabilir):

   ```
   python.org/downloads/windows -> "Windows installer (32-bit)"
   C:\Python312-32 gibi ayri bir dizine kurun
   C:\Python312-32\python.exe -m pip install pywin32
   C:\Python312-32\python.exe main.py --tani
   C:\Python312-32\python.exe main.py
   ```

3. **Yetki farki** — Mach3 yonetici olarak calisiyor, bu program degil
   (ya da tersi). Ikisi birbirini goremez. `cmd`'yi "Yonetici olarak
   calistir" ile acin.
4. **COM sunucusu kayitli degil** — teshisin 4. adiminda iki gorunumde de
   hicbir Mach ProgID'i yoksa, Mach3'u bir kez yonetici olarak acip kapatmak
   genelde kaydi tamamlar.

Teshis ciktisi makineye ozel gizli bilgi icermez, oldugu gibi paylasabilirsiniz.

---

## Tezgah referansi: limitlerin calismasi icin SART

Yumusak limitler (or. X 0..2100, Y 0..4050) **makine koordinatina** gore
denetlenir. Referans switch'i olmayan makinelerde Mach3'un makine sifiri
tezgahin kosesi degil, **Mach3 acildiginda kafanin bulundugu rastgele
noktadir**. Bu yuzden limitler tek basina korumaz: 0..2100 araligi rastgele
bir yerden olculur ve tezgah disina cikabilirsiniz.

> Gecici (is) DRO'yu 2099 yapmak da yardimci olmaz: denetim is DRO'suna
> degil fiziksel konuma bakar. Is DRO'su 2099 gosterirken makine 0.7'de
> olabilir — 2100 mm daha yolunuz var demektir.

**Cozum — tezgah referansi:** ust seritteki **“Referans al”** dugmesine
basip kafanin **su anki fiziksel yerinin tezgah koordinatini** yazin. Tum
denetimler artik su koordinatta yapilir:

```
tezgah = makine_okumasi - referans
referans = makine_okumasi - bildirilen_tezgah_koordinati
```

Iki kullanim sekli var:

| Kafa nerede | Ne yazarsiniz |
|---|---|
| Sifir kosesinde | Hepsi **0** kalsin (varsayilan) |
| Baska bir yerde (or. karsi uc) | O noktanin koordinati, or. **X = 2099** |

Ayni sey DRO penceresinden de yapilabilir: `Ctrl+X` ile acilan pencerede
**“Tezgah koordinati”** kipini secin. Referans yokken bu kip zaten
varsayilan gelir.

> **Is koordinati ile karistirmayin.** DRO'ya “is koordinati” kipinde
> yazdiginiz deger **parca sifiridir** ve yumusak limitleri
> **degistirmez**. X'e 2099 yazip da tezgah disina cikabiliyorsaniz
> sebebi budur — “Tezgah koordinati” kipini kullanin.

Bildirilen deger tanimli tezgah araliginin disindaysa (or. X = 3000 ama
tezgah 0..2100) referans **reddedilir**; yanlis bir referans korumayi
sessizce ise yaramaz hale getirirdi.

Referans alma ve DRO yazma **eksenler dururken** yapilir; hareket
sirasinda okunan konum yazilirken eskiyecegi icin bu islemler reddedilir.

### Makine koordinatinin kendisi degistirilebilir mi?

**Deger yazilamaz.** Mach3 makine DRO'suna (`83/84/85`) `SetOEMDRO` ile
yazmayi **sessizce yok sayar** — gercek makinede olculdu. Yani "makine X'i
2099 yap" diye bir sey yok.

Mach3'te makine koordinatini degistirmenin tek yolu **Ref** islemidir ve
yalnizca **sifirlayabilir**: referans penceresindeki *“Mach3'un makine
koordinatini da sifirla (Ref)”* bolumu, bulundugunuz noktayi makine sifiri
yapar.

* Home switch'i **olmayan** eksende hicbir hareket olmaz; makine
  koordinati oldugunuz yerde 0 olur.
* Home switch'i **tanimli** eksende Ref, switch'i **aramaya gider** —
  eksen hareket eder. Emin degilseniz kullanmayin.
* Kafa yerinden kimildamadigi icin **tezgah koordinatiniz korunur**;
  referans yeni makine okumasina gore kendiliginden tazelenir.
* Ref gercekten isledi mi diye makine DRO'su **geri okunur**. Yanlis OEM
  buton kodu, kapali Ref ya da Reset durumunda islem sessizce basarisiz
  olur; bu durumda hata verilir (`oem_ezme.buton` ile `ref_x/ref_y/ref_z`
  kodlari duzeltilebilir).

**Ne ise yarar:** kafayi sifir kosesine getirip bir kez Ref yaparsaniz
Mach3'un **kendi** yumusak limitleri de dogru yerden olculmeye baslar.
Boylece bu uygulamanin disindan — Mach3 ekrani veya el kumandasi —
yapilan hareketler de korunur. Uygulamanin referans katmani Mach3'e
dokunmaz, o yuzden tek basina bunu saglayamaz.

Arayuzdeki DRO'nun ikinci satiri bu **tezgah** degerini gosterir; limitler,
tezgah tabani ve program kapsam denetimi hep buna bakar.

* Referans **alinmadan hicbir hareket yapilmaz** (jog, MDI, mutlak
  konumlama, program). `guvenlik.referans_zorunlu` ile kapatilabilir; o
  zaman eski (korumasiz) davranisa donulur ve serit kirmizi kalir.
* Mach3 yeniden baslatildiginda makine sifiri kaydigi icin referans
  **kendiliginden dusurulur**; tekrar almaniz gerekir.
* Referansi tek eksen icin de guncelleyebilirsiniz; diger eksenlerin
  referansi bozulmaz. Serit uzerindeki **“Duzelt”** dugmesi ayni pencereyi
  acar.
* Simulasyon arka ucunda makine koordinati tanim geregi tezgah sifirindan
  basladigi icin referans otomatik alinmis sayilir.

**Referanssiz eksende hareket artik reddedilir.** Onceden koordinat
okunamayan eksende limit denetimi sessizce ATLANIYORDU (fail-open);
bu delik kapatildi.

---

## Tezgah tabani: is sifirindan BAGIMSIZ dalma korumasi

Farkli boyda bicaklar takip farkli yuksekliklerde sifirliyorsaniz, "is
sifirinin N mm altina inme" turu bir sinir sizi **korumaz**: ayni sayi her
sifirlamada baska bir fiziksel yuksekli§e denk gelir.

Bu yuzden iki ayri sinir vardir:

| Ayar | Nereye gore | Ne ise yarar |
|---|---|---|
| `makine_z_taban` | **TEZGAH** koordinati (referansa gore) | **Tezgahi korur.** Is sifiri, takim boyu ve aktif profilden bagimsizdir; hicbir komut (jog, MDI, program) bunun altina inemez. |
| `max_dalis_derinlik` | **IS** koordinati | Operator rahatligi: "is sifirimin 60 mm altina elle inme". Tezgah korumasi **degildir**. |

Taban denetimi **jog, mutlak konumlama, MDI ve program** yollarinin dordunde
de uygulanir; programda ihlal varsa uyari degil **hata** verilir ve program
baslatilmaz. Tezgah sekmesinde “tabana kalan” degeri surekli gorunur
(10 mm alti sari, 2 mm alti kirmizi).

**Nasil olculur:** once **referans alin**, sonra takimi tabla yuzeyine deger
konuma indirin → arayuzdeki **TEZGAH** Z degerini okuyun → 1–2 mm pay
ekleyip *Guvenlik > Tezgah tabani* alanina yazin. (“Su anki makine Z'yi
kullan” dugmesi degeri doldurur.)
Olculmediyse denetim yapilmaz ve arayuz “olculmedi” uyarisi gosterir —
yanlis bir taban, olmayan bir tabandan daha tehlikelidir.

### Is sifiri profilleri

Her bicak/malzeme icin is sifirini kaydedip geri cagirabilirsiniz. Profil,
is sifirini **makine koordinatinda** saklar; bu yuzden geri yukleyince
“hangi sayiyi yazmam gerekiyordu” degil, **tam olarak ayni fiziksel
yukseklik** geri gelir. Tezgah sekmesindeki profil kutusundan *kaydet* /
*yukle* yeterlidir.

---

## Kalibrasyon: dijital kumpasla hassas ayar

*Kalibrasyon* sekmesi, **“100 mm dedim, gercekten kac mm gitti?”** sorusunu
olcup makinenizi mikron duzeyinde ayarlamanizi saglar.

> **Onemli:** Mach3'un COM/OLE arayuzunde `Steps per unit` (motor adim
> sayisi) ve `Backlash` degerlerini **okuyan ya da yazan bir cagri yoktur** —
> bu degerler Mach3'un profil dosyasindadir ve yalnizca Mach3'un kendi
> pencerelerinden degistirilebilir. Bu yuzden is bolusumu soyledir:
>
> * **program**: hareketi yapar, olcumu toplar, ortalar, yeni degeri hesaplar
> * **siz**: sag paneldeki sayiyi Mach3'un ilgili kutusuna yazarsiniz
>
> Sag panel **hangi menu → hangi pencere → hangi kutu → hangi sayi**
> bilgisini eksiksiz verir ve “Kopyala” ile panoya alinir.

### Olcum nasil yapilir

1. **Kumpasi baglayin.** Sabit cenesi tezgaha (hareket *etmeyen* parcaya),
   hareketli cenesi kizak/spindle tarafina (hareket *eden* parcaya) gelecek
   sekilde, olcum ekseni dogrultusunda sabitleyin.
2. **Mach3'teki guncel “Steps per” degerini girin**
   (*Config → Motor Tuning and Setup → &lt;eksen&gt; Axis*). Bos birakirsaniz
   program yalnizca **carpan** verir; mevcut degerinizi onunla carparsiniz.
3. **Baslat** → **“Boslugu al”**: eksen once ters yone, sonra olcum yonune
   `on_yaklasma` kadar gider. Boylece vida/kayis boslugu olcum yonunde
   toplanir ve olcege karismaz.
4. **Kumpasi sifirlayin** (ya da A okumasini yazin).
5. **“Olcum hareketini yap”** → eksen tam `olcum_mesafe` kadar gider.
6. **B okumasini girin.** Gercek mesafe = |B − A|.
7. Tekrar sayisi kadar yineleyin; program **ortalar** ve olcumler arasindaki
   **yayilimi** kumpas cozunurluguyle karsilastirip guvenilir olup olmadigini
   soyler.

### Hesap

```
yeni_adim_basi = eski_adim_basi x (komut / olculen)
```

Mach3 `adim_basi x komut` kadar darbe gonderir; olculen mesafe komuttan
**buyukse** adim sayisi fazladir ve **kucultulmelidir**. Ornek: 1600 adim/mm
ile 100 mm komuta 100.5 mm gidiyorsa yeni deger `1600 x 100/100.5 = 1592.04`.

### Uc olcum turu

| Tur | Ne olcer | Mach3'te nereye yazilir |
|---|---|---|
| **Olcek** | Eksenin gercek adim/mm degeri | `Config → Motor Tuning and Setup → <eksen> Axis → Steps per` (sonra **SAVE AXIS SETTINGS**) |
| **Bosluk** | Yon degistirirken kaybolan hareket (backlash) | `Config → Backlash → <eksen>` + *Backlash Enabled* |
| **Dogrulama** | Yeni deger gercekten isledi mi | — (yalnizca rapor; tolerans disindaysa uyarir) |

Bosluk olcumunde ayni noktaya bir kez olcum yonunden, bir kez **ters
yonden** gelinir; iki okuma arasindaki fark o eksenin kayip hareketidir.

### Guvenlik

* Kalibrasyon hareketleri **normal jog gibi** guvenlik katmanindan gecer:
  tezgah referansi zorunlu, yumusak limit, tezgah tabani, adim ve besleme
  siniri denetlenir. Bir hareket reddedilirse oturum basa doner ve **hangi
  limitin asildigi** yazilir.
* Olcum mesafesi + on yaklasma toplami `max_jog_adim` degerini asiyorsa,
  **olcumun ortasinda degil baslarken** reddedilir.
* Kumpas okumasi komuttan %50'den fazla sapiyorsa kabul edilmez — bu bir
  kalibrasyon sapmasi degil, kaymis bir kumpas ya da yanlis girilmis bir
  sayidir.
* **“Mach3'e girdim”** dedikten sonra o eksenin **tezgah referansi dusurulur**:
  adim sayisi degisince makine koordinatinin olcegi de degisir, eski referansla
  olculen limitler artik yanlis yeri gosterir. Yeniden referans alin ve tezgah
  tabanini dogrulayin.

Kaydedilen degerler (`adim_basi`, `bosluk`) ve uygulanan her kalibrasyonun
gecmisi ayar dosyasinda tutulur. Bu kayit **makineyi degistirmez** — makineyi
Mach3'e yazdiginiz deger kalibre eder; kayit yalnizca bir sonraki hesabin
girdisidir.

---

## Jog: hiz, tepkisellik ve durma

Mach3'un COM arayuzunde **gercek bir "surekli jog" cagrisi yoktur**; basili
tut hareketi, kisa artimli adimlarin **zincirlenmesiyle** uretilir. Hissiyati
Mach3'e yaklastiran uc kural:

| Kural | Neden |
|---|---|
| `adim = feed/60 × tik × ortusme` | Her adim, bir sonraki gelene kadar **bitmemeli**. Adim sabit bir tavana (eski surumde 5 mm) sikistirilirsa hizli jogda eksen adimlar arasinda durur — "tik tik" hissiyati. |
| Adim basina **tek** MDI satiri | Eskiden her adimda `G91` + `G1 …` + `G90` diye **uc** COM cagrisi gidiyordu. Artik `G91` basta bir kez, `G90` sonda bir kez gonderilir. |
| Birakinca **Feed Hold + Stop** | Yoksa parmagi cektikten sonra eksen kuyrukta kalan adimi tamamlayana kadar kayar. Feed Hold Mach3'un kendi yavaslama rampasini kullanir, sert kesme olmadigi icin adim kaybi riski dogurmaz. |

### Iki hiz: yon tusu ve Shift + yon

| Giris | Hiz |
|---|---|
| <kbd>←</kbd> <kbd>→</kbd> <kbd>↑</kbd> <kbd>↓</kbd> <kbd>PgUp</kbd> <kbd>PgDn</kbd> | `jog_yavas_feed` (varsayilan **600 mm/dk**) |
| <kbd>Shift</kbd> + ayni tuslar | `jog_hizli_feed` (varsayilan **4000 mm/dk**) |

Basili tutarken <kbd>Shift</kbd>'e basip birakmak hizi **jogu kesmeden**
degistirir (zincir kirilsa eksen bir an dururdu). Ikisi de *Kontrol*
sayfasindaki kutulardan ya da *Guvenlik > Surekli jog hissiyati*'ndan
ayarlanir; `max_jog_feed` her ikisini de ust sinirdan kirpar.

Jog tuslari **klavye kilidi kapaliyken de** calisir — kilit yalnizca
Windows/Alt/Tab gibi tuslari yutmak icindir. Bir yazi alanina yazarken ya da
bir pencere acikken tuslar jog yapmaz.

### Ince ayar (*Guvenlik > Surekli jog hissiyati*)

| Ayar | Varsayilan | Etkisi |
|---|---|---|
| `jog_tik_ms` | 80 | Zincirdeki adim araligi. Kuculdukce tepki artar, COM trafigi de artar. |
| `jog_ortusme` | 1.35 | Adim uzunlugu carpani. Kucultursaniz eksen takilir; buyutursaniz birakinca sonlanma mesafesi uzar. |
| `jog_birakinca_durdur` | acik | Birakinca Feed Hold + Stop ile kuyrugu bosaltir. |

Iki koruma her adimda ayrica gecerlidir: **kalp atisi** (istemci susarsa
hareket durur) ve **yumusak limit**. Limit denetimi, DRO'dan okunan degil
**gonderilen** konuma gore yapilir — yoklama 100 ms geriden geldigi icin, uzun
adimlarda okunan konuma guvenmek siniri sessizce astirabilirdi.

Program **calisirken** surekli jog reddedilir (birakinca gonderilen Feed
Hold/Stop, calisan programi keserdi).

---

## Klavye kisayollari

| Tus | Islem |
|---|---|
| <kbd>Ctrl</kbd>+<kbd>X</kbd> / <kbd>Y</kbd> / <kbd>Z</kbd> | O eksenin DRO'suna deger yaz (Mach3'teki gibi) |
| <kbd>F10</kbd> / <kbd>F11</kbd> | Besleme override (FRO) azalt / artir |
| <kbd>Space</kbd> | Feed hold |
| <kbd>Esc</kbd> | Acil durdur |
| Oklar / <kbd>PgUp</kbd> <kbd>PgDn</kbd> | Jog (klavye kilidi acikken) |

Ctrl zorunlulugu *Ayarlar > Klavye kisayollari*'ndan kapatilabilir; kapaliyken
dogrudan <kbd>X</kbd>/<kbd>Y</kbd>/<kbd>Z</kbd> yeterlidir. Besleme tuslari ve
adimi (%) de oradan degistirilir. Bir yazi alanina yazarken kisayollar her
durumda devre disidir.

---

## Tek ekran (Tezgah sekmesi)

Klavyeyle calisildigi icin dugmeler kucuk tutulmustur; ekranin buyuk kismi
bilgiye ayrilmistir. Ayni ekranda:

* **Koordinatlar** — is ve makine degerleri yan yana, tabana kalan pay
* **Takim yolu onizlemesi** — kesim sirasi numaralariyla (tekerlekle yakinlastir,
  surukleyerek kaydir, cift tiklayarak sigdir)
* **Kesim sirasi listesi** — blok secince onizleme ve sure gunceller
* **Ilerleme** — gecen / kalan / tahmini sure, kat edilen yol
* **Besleme** — FRO, F ve S degerleri

---

## Is raporu ve metrikler

Program calisirken gecen sure, duraklamalar, kat edilen yol ve blok bazli
sureler toplanir. Program bitince *Rapor* sekmesinden goruntulenir ve
**PDF / CSV / JSON** olarak indirilir.

PDF, ek kutuphane olmadan uretilir (`rapor.py` icinde kucuk bir PDF yazici;
projenin “makineye ek bagimlilik kurma” kuralina uymak icin). Rapor icerigi:
baslangic/bitis, gecen ve calisan sure, duraklama sayisi, tahmine gore sapma
orani, kat edilen yol, kullanilan profil, tezgah tabani, blok tablosu
(tahmini / gercek / fark) ve olay dokumu.

**Ilerleme nasil olculuyor?** Mach3'un COM betik arayuzunde calisan satiri
veren bir fonksiyon **yoktur** (makinede yoklandi: `GetCurrentLine` ve
benzerleri bu surumde yok). Bu yuzden ilerleme, DRO'dan okunan konumla
**kat edilen yoldan** hesaplanir ve blok sinirlariyla eslestirilir. Toplam
sure ve toplam yol her zaman dogrudur; ayni yerden tekrar gecen yollarda
blok eslestirmesi kayabilir.

---

## Dosya secme ve onizleme olculeri

### Gezgin

* **Kaldiginiz yerden acilir.** Gezgin, en son *bakilan* klasoru hatirlar —
  dosya acmadan cikmis olsaniz bile. (Bu, en son *calisilan* klasorden
  ayridir; ona da listenin basindaki "Son calisilan klasor" kisayoluyla
  donersiniz.)
* **En son degisiklik yapilan en ustte.** Varsayilan siralama tarihe goredir,
  klasorler de dosyalar da yeniden eskiye dizilir; CAM'den yeni cikan is hep
  ilk siradadir. Ust kosedeki secimle "Ada gore"ye gecebilirsiniz, secim
  hatirlanir.
* **Suzgec** listenin ustundedir ve istemcide calisir (her tusa basista
  sunucuya gitmez); <kbd>Enter</kbd> ilk eslesmeyi acar, <kbd>Esc</kbd>
  suzgeci temizler.
* Klasor okuma `os.scandir` ile **tek gecistir**; onceki surum her girdi icin
  ayri `isdir` + `stat` cagiriyordu ve ag surucusunde (`\\sunucu\isler`)
  gezgin gozle gorulur sekilde takiliyordu.

### Kesim alani olculeri

Takim yolu onizlemesinin altinda, malzemeyi baglamadan once gereken olculer
yazar:

| Olcu | Anlami |
|---|---|
| **kesim alani** | Yalnizca kesim bloklarinin kapladigi alan (`genislik × yukseklik`), yaylarin gercek gecisleri dahil |
| **X / Y** | Is koordinatinda kesimin ulastigi aralik |
| **en sag / en ust** | X0 ve Y0'a gore en uzak noktalar |
| **X0Y0'dan** | Parcanin sol-alt kosesinin is sifirina uzakligi |
| **kosegen** | Kesim alaninin kosegeni |
| **Z** | En derin ve en yuksek Z |
| **hareket zarfi** | Bosta (G0) gidisler **dahil** tum hareket alani — yumusak limitleri asan genelde budur, kesim alani degil |

Onizlemede ayrica kesim alani **kesikli mavi bir kutuyla** cizilir (ustunde
genislik, saginda yukseklik) ve is sifiri **kirmizi bir arti + "X0 Y0"**
etiketiyle isaretlenir; boylece parcanin sifira gore nerede durdugu tek
bakista gorunur.

---

## Yeni dosya izleme

Isleriniz tek bir kok klasorun altinda ic ice klasorlerdeyse
(or. `D:\isler\30-01-2026\firma-XXX\001-malzemeX\001.tap`), *Ayarlar > Yeni
dosya izleme*'ye kok klasoru yazin. CAM'den yeni bir dosya cikinca ust
seritte “Yeni G-Code dosyasi: … [Ac] [Atla]” gorunur. **Onay vermeden
hicbir dosya yuklenmez.**

Dosya hala yaziliyor olabilecegi icin (CAM kaydediyordur) bir dosya, boyutu
iki ardisik taramada ayni kalana kadar sorulmaz. Program acilisinda eski
dosyalar sorulmaz; yalnizca son 2 saat (ayarlanabilir) icinde degismis
olanlar “yeni” sayilir.

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
| **Kontrol** | DRO (is + makine), jog padi (adim / basili tut), **yavas/hizli jog hizlari**, hassas hareket, kaydirmali sifirlama, klavye kilidi, MDI |
| **Program & Sira** | Dosya secme, numarali kesim sirasi, takim yolu onizlemesi, **kesim alani olculeri**, sure tahmini, guvenlik denetimi, baslat/feed hold/durdur |
| **Kalibrasyon** | Kumpasla adim/mm (Steps per unit) ve bosluk olcumu, Mach3'e girilecek degerler |
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
    "jog_yavas_feed": 600, "jog_hizli_feed": 4000,   // yon tusu / Shift+yon
    "jog_tik_ms": 80, "jog_ortusme": 1.35,           // surekli jog akiciligi
    "jog_birakinca_durdur": true,
    "guvenli_z": 20, "max_dalis_derinlik": -60,
    "max_surekli_jog_sn": 30, "jog_kalp_atisi_ms": 400,
    "pin": "……"
  },
  "klavye": { "kilit_acilista": false, "odak_bekcisi": true,
              "odak_pencere_baslik": "Mach3" },
  "kalibrasyon": {
    "adim_basi": { "X": null, "Y": null, "Z": null },  // Mach3'teki Steps per
    "bosluk":    { "X": null, "Y": null, "Z": null },  // olculen backlash (mm)
    "olcum_mesafe": 100, "olcum_feed": 300, "on_yaklasma": 5,
    "tekrar": 3, "kumpas_cozunurluk": 0.01
  }
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
│   ├── is_takip.py                # gecen sure, ilerleme, blok sureleri
│   ├── rapor.py                   # PDF/CSV/JSON rapor (kutuphanesiz PDF)
│   ├── izleyici.py                # yeni G-Code dosyasi izleme
│   ├── guvenlik.py                # limitler, kalp atisi, kontrol kilidi, gunluk
│   ├── klavye_kilidi.py           # Windows klavye hooku + odak bekcisi
│   ├── gcode.py                   # blok ayristirma, kapsam, bloktan baslatma
│   ├── sure.py                    # kesim suresi tahmini
│   ├── kalibrasyon.py             # kumpasla adim/bosluk olcumu + Mach3 talimati
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
python tests/test_jog.py           # surekli jog: adim uzunlugu, tek satir MDI, birakinca durma
python tests/test_kalibrasyon.py   # kumpas olcumu, adim/mm hesabi, Mach3 talimati
python tests/test_web.py           # HTTP API + olay akisi
```

---

## Sinirlar

* Gercek baglanti icin **Windows + Mach3 + pywin32** gerekir; digerlerinde
  yalnizca simulasyon calisir.
* Mach3'un COM arayuzunde guvenilir bir "surekli jog" cagrisi yoktur; basili
  tut modu, **kisa artimli adimlarin** zincirlenmesiyle uretilir (bkz.
  "Jog: hiz, tepkisellik ve durma"). Her adim ayri ayri denetlenir, kalp
  atisi kesilince zincir aninda durur ve birakinca kuyruk Feed Hold + Stop
  ile bosaltilir. Yine de araya bir ag turu girdigi icin tepki, Mach3'un
  kendi klavye jogundan **bir tik** daha gectir.
* Program calisirken "kacinci bloktayim" bilgisi Mach3'ten okunmaz; ilerleme
  cubugu yerine **tahmini sure** gosterilir.
* Mach3'un COM arayuzu **`Steps per unit` ve `Backlash` degerlerini ne okur ne
  yazar.** Kalibrasyon sekmesi olcumu ve hesabi yapar, degeri Mach3'un ilgili
  penceresine **operator** girer.

---

## Lisans

MIT — bkz. [LICENSE](LICENSE).
