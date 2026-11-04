# Mach3-Kontrol

Mach3'ün **yerine geçmeyen**, **yanında çalışan** bir kontrol arayüzü. Mach3
makineyi sürmeye devam eder; bu program ona COM üzerinden bağlanır ve Mach3'ün
**Program Run ekranını** web arayüzünde yeniden kurar: LED şeridi, Zero/REF'li
DRO'lar, Jog ON/OFF · Cont/Step · Slow Jog Rate % · Step paneli, canlı satırlı
G-Code penceresi, takım yolu, Cycle Start / Feed Hold / Stop / Reset.
Her komut makineye **gitmeden önce** bir güvenlik denetiminden geçer.

* **Mach3 gibi jog** — Continuous ve Step kipleri, Slow Jog %, Shift = %100,
  Jog ON/OFF, bırakınca **yavaşlayarak** durma, limite yavaşlayarak varma.
* **Mach3 ekranı** — tek ekranda DRO, jog, G-Code, takım yolu ve çalıştırma
  düğmeleri; Reset'te yanıp sönen kırmızı düğme; telefondan da kullanılır.
* **Kaza engelleyici güvenlik** — yumuşak limitler, tezgâh referansı, tezgâh
  tabanı, ölü adam butonu (kalp atışı), program öncesi kapsam denetimi,
  tek-operatör kilidi, **her harekette mm birimi zorlaması**.
* **Kesim sırası** — bloktan güvenli yeniden başlatma, süre tahmini, iş raporu.
* **Kumpasla kalibrasyon**, yeni dosya izleme, iş sıfırı profilleri.

Python standart kütüphanesi yeter; yalnızca gerçek Mach3 bağlantısı için
`pywin32` gerekir.

---

## ⚠️ Önce güvenlik

Bu program **bir güvenlik cihazı değildir.**

* **Donanım acil-stop butonunun ve limit switch'lerin yerine geçmez.**
  Yazılım çökebilir, Windows donabilir, ağ kopabilir.
* Yumuşak limitler **siz girene kadar** örnek değerlerdedir; arayüz bunu
  turuncu şeritle hatırlatır. **Güvenlik** sekmesinden gerçek ölçülerinizi
  girip "doğruladım" kutusunu işaretleyin.
* İlk denemeyi **malzeme ve takım olmadan**, tercihen `--simulasyon` modunda
  yapın. Gerçek makinede ilk jog'u yavaş (%5–10) yapın.
* Mach3'ün kendi jog'u hakkında (aşağıda) **donanımda doğrulanmamış** noktalar
  vardır; ne olduğu açıkça yazılıdır.

---

## Kurulum

Makinenin (Mach3'ün çalıştığı) Windows bilgisayarına:

```bash
pip install pywin32          # yalnızca gerçek Mach3 bağlantısı için
python main.py               # web arayüzü, http://127.0.0.1:8010
python main.py --simulasyon  # Mach3 olmadan dene (sanal tezgâh)
```

Konsol şunu yazar:

```
  Bu bilgisayardan : http://127.0.0.1:8010
  Telefondan       : http://192.168.1.34:8010
  PIN              : 481902
```

Telefondan o adrese girip **PIN**'i yazarsınız (`~/.mach3_kontrol/ayarlar.json`).
**Sunucuyu internete açmayın**; ev/atölye ağı için tasarlanmıştır.

> **Mach3 32-bit'tir.** Mach3 COM kaydını kayıt defterinin 32-bit görünümüne
> yazar; **64-bit Python bunu göremez** (`Invalid class string`). 32-bit Python
> kurun. Teşhis için: `python main.py --tani`.

---

## Mach3 ile bağlantı

**Mach3 tarafına hiçbir şey kurulmaz** (eklenti, makro, brain, screenset yok).
Kullanılan, Mach3'ün kendi OLE/COM otomasyon arayüzüdür (ProgID
`Mach4.Document` — Mach3'te de böyledir).

### Mach3'ü program açar — elle açmayın

Mach3 COM arayüzünü **yalnızca `/Automation` anahtarıyla** açar; elle açılan
bir Mach3'e bağlanmak **mümkün değildir**. Bu yüzden program Mach3'ü kendisi
başlatır:

```
C:\Mach3\Mach3.exe /p Mach3Mill /Automation      (çalışma dizini: C:\Mach3)
```

| Ayrıntı | Atlanırsa |
|---|---|
| `/Automation` | COM arayüzü hiç açılmaz |
| Çalışma dizini = Mach3 klasörü | "Corrupt install, no license file found" |
| `/p <profil>` | Mach3 profil seçim penceresinde bekler |

Her Mach3 açılışına **yalnızca bir istemci** bağlanabilir (sınıf fabrikası tek
kullanımlık). Programı yeniden başlatırsanız Mach3'ü de kapatıp yeniden
açtırmanız gerekir. Ayarlar: `mach3_baslat`, `profil`, `mach3_exe`,
`acilis_zaman_asimi_sn`.

### OEM kodları artık tahmin değil

Mach3 kurulumundaki **`DROs.txt`, `Buttons.txt`, `Leds.txt`** sıralı listelerdir
ve OEM kodu listedeki sıradan gelir:

```
DRO    kodu = 800  + sıra      LED  kodu = 800  + sıra      Düğme kodu = 1000 + sıra
```

Bu, gerçek bir Mach3'te **salt-okunur** yoklanarak doğrulandı
(`GetOEMDRO(828)` = Jog Increment = profildeki `JogInc`, `GetOEMLED(800)` =
Reset = `IsEstop`, `GetOEMDRO(83)` = `GetAbsPosition(0)`). Program bağlanırken
kurulumdaki üç dosyayı okur ve kodları **kendi sürümünüze göre uyarlar**; gömülü
listeden fark varsa günlüğe yazar.

Önceki sürümde bulunan **yanlış kodlar düzeltildi**:

| İşlev | Eski (yanlış) | Doğru |
|---|---|---|
| **Stop** | 1002 = *Rewind* | **1003** |
| Rewind | 1003 = *Stop* | **1002** |
| Ref X / Y / Z (Home) | 22 / 23 / 24 | **1022 / 1023 / 1024** |
| Zero X / Y / Z | 131 / 132 / 133 | **1008 / 1009 / 1010** |
| Reset (E-Stop) LED | 825 = *Probe* | **800** |
| Spindle Speed DRO | 812 = *OEM Code* | **817** |

Ayarlar › Mach3 OEM kodları panelinde hepsi görünür ve ezilebilir.

### Sıfırlama Mach3 Reset durumundayken ÇALIŞMAZ

Mach3 **Reset (E-Stop)** durumundayken `SetOEMDRO`'yu **sessizce yok sayar**.
Bu yüzden sıfırlama, profil yükleme ve besleme override'ı her yazımdan sonra
**geri okunup doğrulanır**; yazılamadıysa açık hata verilir. Reset'te hiçbir
hareket komutu da gönderilmez.

---

## Birim: program mm'de çalışır

Mach3 **G20 (inç) kipinde açılabilir** — bu bilgisayardaki Mach3 tam olarak
böyle (*Standard Meas.* LED'i yanık). Uygulama her sayıyı mm sandığı için
`G1 X10`, 10 **inç** (254 mm) hareket ettirirdi ve yumuşak limit denetimi bunu
göremezdi.

Bu yüzden **her harekette** (jog, step, konumlama, MDI, program) Mach3'ün
birimi LED'lerden (801/802) okunur; inçteyse `G21` gönderilir ve mm'ye
geçtiği doğrulanır, **geçmezse hareket reddedilir**. LED şeridinde birim
(`mm` yeşil / `G20 İNÇ` sarı) sürekli görünür. `guvenlik.birim_zorla_mm` ile
kapatılabilir (önerilmez).

---

## Jog — Mach3'ün modeli

Mach3'te jog şöyle çalışır; bu program aynısını uygular:

| Özellik | Davranış |
|---|---|
| **Jog ON/OFF** (<kbd>Tab</kbd>) | Kapalıyken hiçbir jog tuşu çalışmaz |
| **Continuous** | Tuş basılıyken eksen **Slow Jog Rate % × eksen azami hızıyla** gider; bırakınca kendi ivmesiyle **yavaşlayarak** durur |
| **Step** | Bir basış = tam **Step** mesafesi; tuşu tutmak tekrar etmez. Hareket sürerken yapılan basışlar sınırlı tampona alınır (Mach3'teki `IncBuffer`, varsayılan 5) |
| **Shift** | Hız **%100** olur; basılıyken Shift'e basıp bırakmak jogu **kesmeden** hızı değiştirir |
| **Cycle Jog Step** | Step değerini listede dolaştırır (`10, 1, 0.1, 0.01, 0.001`) |
| **Yumuşak limit** | Eksen limite **yavaşlayarak varır** ve orada durur |

Mach3'ün profil dosyasındaki tuş atamasıyla aynıdır: <kbd>→</kbd>/<kbd>←</kbd> X±,
<kbd>↑</kbd>/<kbd>↓</kbd> Y±, <kbd>PgUp</kbd>/<kbd>PgDn</kbd> Z± (`AxisKeys`).
Ek tuşlar: <kbd>Tab</kbd> Jog ON/OFF · <kbd>M</kbd> Cont/Step · <kbd>,</kbd>
<kbd>.</kbd> Step · <kbd>[</kbd> <kbd>]</kbd> Slow Jog ∓5%. Jog tuşları yalnızca
**Mach3** ve **Ofsetler** sekmelerinde çalışır (diğer sayfalarda ok tuşları
listeleri kaydırır). Aynı anda **tek eksen** jog edilir.

**Hız modeli:** `hız = Slow Jog % × eksen azami hızı`. Eksen azami hızı ve
ivme **Ayarlar › Makine**'de, Mach3 *Motor Tuning* (Velocity / Acceleration)
değerlerinizle **aynı** olmalıdır — jog hızı ve durma mesafesi bunlardan
hesaplanır. (Mach3 bu değerleri COM'dan vermez.)

### İki jog sürücüsü

**1) Artımlı (varsayılan — her Mach3'te çalışır).** Mach3'ün COM arayüzünde
"basılı tut" çağrısı yok; hareket Mach3'e **mutlak** kısa hedefler
zincirleyerek üretilir. Zincir, ekseni **durma mesafesinden uzun** bir yolu hep
kuyrukta tutar (aksi halde Mach3'ün planlayıcısı hedef hıza hiç ulaşamaz ve
eksen "tik tik" gider). Bırakınca **Feed Hold** → hareket durunca **Stop**:
yavaşlama Mach3'ün kendi ivmesiyle olur, yani Mach3'ün kendi tuş bırakışıyla
aynı **durma mesafesi** (`v²/2a`) elde edilir. Mutlak hedef kullanıldığı için
`G91` gibi kalıcı bir kip bırakılmaz. Konum geri bildirimi her turda taze
okunur.

**2) Mach3'ün kendi jog motoru (isteğe bağlı, deneysel).** COM'da
`JogOn(short eksen, short yön)` / `JogOff(short eksen)` vardır (imzalar
`Mach3.exe`'nin dispatch map'inden okundu). Bu sürücüde jog'u Mach3'ün kendisi
yapar. Ancak `yön` değerinin hangi işarete denk geldiği belgesiz ve **eksene
göre farklı olabilir** (X ters kodlanmış görünüyor). Bu yüzden:

1. **Ayarlar › Mach3 tanılama › "Mach3 jog doğrulama"** her eksende iki
   değeri ~0,35 sn, **%5 hızla** dener, DRO'nun hangi yöne gittiğine bakıp
   eşlemeyi **öğrenir** (Slow Jog Rate önce %5'e yazılıp geri okunur; yazılamazsa
   **hiç hareket edilmez**; eksenin iki yönde ≥ 6 mm boşluğu olmalı).
2. Başarılıysa **Güvenlik › Jog sürücüsü** "Mach3'ün kendi jog'u" seçilebilir.
3. Her jog'da ilk 0,3 mm içinde yön kontrol edilir; ters giderse **hemen
   kesilir** ve doğrulama geçersiz kılınır.

Step kipi ve doğrulanmamış durumlar her zaman artımlı sürücüyle yapılır.

> **Dürüst not:** Bu ortamda gerçek Mach3 ile yalnızca **salt-okunur**
> doğrulama yapılabildi (bağlantı, tüm DRO/LED okumaları, metot imzaları,
> Reset'teyken jog'un reddedilmesi). **Fiziksel hareket donanımda
> denenmedi.** Önerilen ilk deneme: malzemesiz, %5–10 Slow Jog ile artımlı
> sürücüde her eksende kısa Continuous ve Step jog yapıp durma mesafesini
> Mach3'ün kendi ok tuşu jog'uyla karşılaştırmak.

### Durdurma

* **Feed Hold** (<kbd>Space</kbd>), **Stop** — kontrol kimde olursa olsun,
  her cihazdan, her an çalışır.
* **ACİL DURDUR** (<kbd>Esc</kbd>) — jog + Feed Hold + Stop, ardından Mach3'ün
  **Reset**'i (Mach3'te Esc = Reset). Reset bir **terslemedir**: Mach3 zaten
  Reset'teyse **basılmaz**, çünkü tekrar basmak E-Stop'u kaldırırdı.
  `guvenlik.acil_reset` ile kapatılabilir.
* Kalp atışı kesilirse, sayfa gizlenirse ya da pencere odağı giderse jog
  **yavaşlayarak** durur (Reset'e basmaz).
* Durdurma komutları COM kuyruğunda **önceliklidir**: okuma yığınının
  arkasında beklemez. Günlük dosyasına yazma arka plandadır, durdurma yolu
  diske hiç dokunmaz.

---

## Mach3 ekranı

| Bölge | İçerik |
|---|---|
| **LED şeridi** | RESET (yanıp söner) · IDLE · RUN · PAUSE · CV · SOFT · JOG CONT/STEP/OFF · birim · REF · SİM |
| **Durum satırı** | Mach3'ün alt mesaj çubuğu: son olay/hata |
| **KONUM** | Zero X/Y/Z · iş koordinatı DRO'su · tezgâh koordinatı · REF · tabana kalan |
| **HOME** | Adlandırılmış iş sıfırları: seç · Uygula · Uygula + git · Kaydet (aşağıda) |
| **BESLEME** | FRO − / + / 100 (<kbd>F10</kbd>/<kbd>F11</kbd>) · F · S |
| **MİL / SOĞUTMA** | CW/CCW/dur + devir · sel/sis/kapat |
| **Takım yolu** | Katmanlı önizleme: vektör sırası, kenar boşlukları, canlı takım konumu (aşağıda) · katlanır · tam ekran · ayrı pencere |
| **G-CODE** | Satır numaralı pencere; Mach3'ün *Current Line* DRO'suyla (816) **yürütülen satır vurgulanır** |
| **JOG** | Jog ON/OFF · Cont/Step · Slow Jog Rate % · Step · ok düğmeleri · Shift |
| **Alt** | Cycle Start · Feed Hold · Stop · Reset · Rewind · Single Block · Denetim · Başlat |
| **MDI** | Yumuşak limitlere göre denetlenen elle G-Code |

**Cycle Start** güvenli akışı izler: program yüklüyse önce denetim, sonra onay,
sonra başlatma; Feed Hold'daysa devam eder. Ofsetler sekmesinde hassas hareket
("X'te 25 mm sola"), kaydırmalı sıfırlama ("burası 5 mm sağı yeni X sıfırı"),
Home noktaları (adlandırılmış iş sıfırları) ve tezgâh referansı vardır.

Telefonda DRO ve jog üstte, takım yolu altta; jog düğmeleri büyür, Shift için
ekran düğmesi vardır.

---

## Takım yolu önizlemesi

Mach3 ekranında ve Program sekmesinde aynı önizleme vardır.

* **Aç/kapat:** panel başlığındaki ▾ düğmesi önizlemeyi katlar (hatırlanır).
* **Tam ekran (⛶)** ve **ayrı pencere (↗ Pencere):** ayrı pencere ikinci
  monitöre taşınabilir; canlı takım konumunu ve programı sunucudan kendisi
  alır. Gerçek tam ekran için pencerede <kbd>F</kbd>. Tam ekranda ana ekranın
  üst çubuğu örtüldüğü için **her tam ekranda kendi ACİL DURDUR düğmesi** vardır.
  Tarayıcı tam ekranı reddederse önizleme pencereyi kaplar.
* **Katmanlar** (tek tıkla açılır/kapanır, tarayıcıda hatırlanır ve açık tüm
  pencereler arasında **canlı eşitlenir**): kesim yolu · taşıma (G0) · blok no ·
  **vektör sırası** · **Z geçişleri** · vektör uzunluğu · yön · **kenar
  boşlukları** · G0 zarfı · tezgâh sınırı · kesim alanı · iş sıfırı · takım ·
  ızgara. *hepsi / hiçbiri / varsayılan* kısayolları vardır.
* Tekerlek yakınlaştırır, sürükleme kaydırır, **çift tık** yol ↔ tezgâhın tamamı
  arasında geçer.

**Vektör.** Takımın güvenli Z'nin **altına indiği andan tekrar güvenli Z'ye
çıktığı ana** kadar olan yoldur; her güvenli Z yükselişi bir vektörü kapatır.
Numaralar dosyadaki gerçek gezinme sırasıdır; *Z geçişleri* katmanı bir
vektörün sonundan sonrakinin başına güvenli Z'deki geçişi oklu çizgiyle gösterir.
(Blok kuralı — "X-Y'li G0 ile başlar" — bir blokta birden çok yükseliş olabilen
dosyalarda daha kaba kalır; vektör her yükselişi ayırır.) Güvenli Z önce
dosyadan bulunur (ilk `G0 Z…` hedefi; kısa "köprü" yükselişleri sayılmaz), yoksa
Ayarlar'daki güvenli Z kullanılır. G91 içeren dosyalarda vektör çıkarılamaz.

**Kenar boşlukları.** Takım yolunun (isteğe bağlı olarak G0 zarfının) dört
kenarından — sol, sağ, alt, üst — **tezgâh sınırına kalan mesafe** çizilir ve
alttaki şeritte yazılır. Tezgâh sınırı, yumuşak limitlerin (pay düşülmüş)
**şu anki iş sıfırına göre** karşılığıdır: Home değiştirince boşluklar da
değişir. 20 mm'nin altı sarı, **negatif kırmızı** ("AŞIYOR") görünür. Böylece
malzemeyi ve Home'u yerleştirirken işin tezgâha sığıp sığmadığı program
başlamadan görülür. Tezgâh referansı yoksa uyarı yazar.

---

## Tezgâh referansı ve taban

Yumuşak limitler **makine koordinatına** değil **tezgâh koordinatına** göre
denetlenir. Referans switch'i olmayan makinede Mach3'ün makine sıfırı tezgâhın
köşesi değil, Mach3 açıldığında kafanın bulunduğu rastgele noktadır. Kafanın
şu anki yerinin **tezgâh koordinatını** bildirirsiniz (sıfır köşesindeyse 0):

```
tezgâh = makine_okuması − referans
```

Referans **alınmadan hiçbir hareket yapılmaz** (jog, MDI, konumlama, program);
Mach3 yeniden başlayınca ve adım/mm değişince referans **düşürülür**. İş DRO'su
(parça sıfırı) limitleri **değiştirmez**. Referans alma ve DRO yazma eksenler
**dururken** yapılır.

`makine_z_taban` (tezgâh Z'si) iş sıfırından ve takım boyundan **bağımsız**
mutlak dalma korumasıdır; jog, konumlama, MDI ve programda uygulanır, program
ihlalinde **hata** verir.

### Home noktaları — adlandırılmış iş sıfırları

Tezgâhın farklı yerlerindeki farklı malzemeleri işlemek için istediğiniz kadar
**Home** tanımlarsınız; her birine takma ad verirsiniz (*Levha A*, *18 mm MDF –
sağ üst*). Tek tıkla geçiş yaparsınız.

| Nerede | Ne yapılır |
|---|---|
| **Mach3 ekranı › HOME** | Home seç · **Uygula** · **Uygula + git** (sıfıra güvenli Z ile gider) · **+ Kaydet…** · Yönet |
| **LED şeridi** | `HOME: Levha B` — şu an **gerçekten** geçerli olan Home |
| **Ofsetler › Home noktaları** | Tüm Home'ların tablosu (X/Y/Z, malzeme, not, durum) · Uygula · Düzenle · Sil |

* **Kaydet:** o an Mach3'te ayarlı iş sıfırını alır (*Mevcut sıfırı al*) **ya da**
  koordinatları elle yazarsınız (bağlantı gerekmez). Boş bırakılan eksen
  saklanmaz ve uygulanınca o eksene dokunulmaz (ör. yalnızca Z'si farklı bir
  malzeme). İsteğe bağlı: malzeme, not, takım boyu, güvenli Z.
* **Koordinat tezgâh koordinatıdır** (`tezgâh = makine − referans`). Mach3'ü
  yeniden açıp referansı aldıktan sonra da her Home **aynı fiziksel noktaya**
  döner; makine koordinatı her açılışta kaydığı için ona göre saklamak ertesi
  gün Home'u başka bir noktaya düşürürdü. Bu yüzden uygulamak için **tezgâh
  referansı** gerekir.
* **Uygula:** `iş = tezgâh − home_sıfırı` DRO'ya yazılır ve **geri okunarak
  doğrulanır**; Mach3 Reset'teyse ya da eksenler hareket halindeyse reddedilir.
  Makine hareket etmez, yalnızca DRO değerleri değişir. **Uygula + git**
  ardından X0 Y0'a (önce güvenli Z) gider; gidiş reddedilirse (ör. limit dışı)
  Home yine de uygulanmış olur ve sebep gösterilir.
* **Aktif Home** saklanan ada değil, **Mach3'ün gerçek sıfırına** bakılarak
  bulunur: sıfırı elle değiştirirseniz Home "aktif" görünmez (tabloda
  *uygulanmadı* ve farkı fare imleciyle görürsünüz). İş raporuna da bu ad yazılır.
* Ad benzersizdir (büyük/küçük harf duyarsız); aynı ad için üzerine yazmak
  onay ister. Home'un sıfırı tezgâh aralığı dışındaysa uyarı verilir.
* Önceki sürümün "iş sıfırı profilleri" otomatik Home olur (**eski kayıt**
  rozetiyle; koordinatlarını doğrulayın). Eski sürümdeki `profil_uygula`,
  referans 0 değilken **yanlış sıfır yazıyordu**; düzeltildi.

Mach3'ün **makine** koordinatına değer yazılamaz (`SetOEMDRO` yok sayılır);
tek yol **Ref**'tir ve yalnızca sıfırlayabilir. Home switch'i **tanımlı** eksende
Ref, switch'i aramaya gider (eksen hareket eder) — emin değilseniz kullanmayın.

---

## Kumpasla kalibrasyon

Mach3 COM'u `Steps per unit` ve `Backlash`'ı **ne okur ne yazar**. Program
hareketi yapar, ölçümü toplar, ortalar ve **Mach3'ün hangi penceresine hangi
sayıyı** yazacağınızı söyler; son yazma işlemi sizdedir.

```
yeni_adım_başı = eski_adım_başı × (komut / ölçülen)
```

Üç tür: **Ölçek**, **Boşluk** (backlash: aynı noktaya iki yönden gelinir),
**Doğrulama**. Hareketler normal jog gibi güvenlik katmanından geçer; kumpas
okuması komuttan %50'den fazla saparsa kabul edilmez; "Mach3'e girdim"
denince o eksenin tezgâh referansı düşürülür.

---

## Program, sıra ve rapor

* **Kesim sırası** — [Cnc-Assistant](https://github.com/ac2f/Cnc-Assistant)
  ile aynı blok kuralı (*blok = X ve Y içeren bir G0 ile başlar*). Bir bloğa
  tıklayıp **oradan güvenle başlatırsınız**: önce güvenli Z, modal durum, hedef
  bloğun başına havadan gidiş, seçili bloklar aynen, orijinal program sonu.
  `G91` içeren dosyalarda bloktan başlatma **reddedilir**.
* **Süre tahmini** — trapez hız profili (`t = d/v + v/a`), köşe kaybı, G4,
  takım değişimi, FRO.
* **İlerleme** — Mach3'ün *Run* LED'i ve *Current Line* DRO'su okunur
  (LED yoksa hareketsizlikten tahmin edilir).
* **Rapor** — PDF / CSV / JSON (ek kütüphane yok).
* **Dosya gezgini** — kaldığınız klasörden açılır, en yeni en üstte, istemci
  tarafı süzgeç.
* **Yeni dosya izleme** — kök klasörün altında CAM'den yeni dosya çıkınca
  "Aç / Atla" sorulur; **onay vermeden hiçbir dosya yüklenmez**.

---

## Güvenlik katmanı (özet)

| Denetim | Açıklama |
|---|---|
| Reset | Reset'te hareket yok; jog sırasında Reset basılırsa jog kesilir |
| Yumuşak limit / referans / taban | Tezgâh koordinatında; belirsizse **reddeder** (fail-closed) |
| Birim | Her harekette mm zorlanır |
| Step/besleme | `max_jog_adim`, `max_jog_feed` |
| Kalp atışı | Sürekli jog istemciden ~400 ms'de bir "hâlâ basılıyım" ister; **ilk atışa tolerans** tanınır |
| Süre sınırı | `max_surekli_jog_sn` |
| Program çalışırken jog | Yasak (bırakınca gönderilen Feed Hold/Stop programı keserdi) |
| Kontrol kilidi | Aynı anda tek cihaz komut verir |
| Olay günlüğü | Her komut zaman damgasıyla `olaylar.log`'a |

### Ağ güvenliği

Yerel makineden (127.0.0.1) gelen istekler PIN'siz kabul edilir. Bu yüzden:

* **CSRF**: `Origin` ve `Sec-Fetch-Site` denetlenir, POST'lar yalnızca
  `Content-Type: application/json` kabul eder. Başka bir sitenin sayfasından
  `fetch("http://127.0.0.1:8010/api/...")` ile makineyi oynatmak engellenir.
* **DNS rebinding**: `Host` başlığı yalnızca IP, `localhost`, `*.local` ya da
  bu makinenin adı olabilir.
* Uzaktan (telefon) `mach3_exe`, `progid`, `oem_ezme`, `web` ayarları
  **değiştirilemez** (aksi halde `mach3_exe` başka bir programa çevrilip çalıştırılabilirdi).
* İstek gövdesi 1 MB ile sınırlıdır; statik dosyalar web klasörü dışına çıkamaz.

### Klavye kilidi (Windows)

Jog sırasında yanlışlıkla **Windows/Alt/Tab**'a basmak Mach3'ün odağını
kaybettirir ve eksen odak gelene kadar durmaz. Kilit açıkken düşük seviye
klavye hook'u izinli tuşlar dışındakileri yutar; ayrıca odak bekçisi Mach3'ün
odağı gittiğini ~100 ms'de fark edip hareketi durdurur (Reset'e basmadan).
**Ctrl+Alt+Del engellenemez.** Hook, Mach3 yönetici olarak çalışıyorsa bu
programın da yönetici olmasını gerektirebilir.

---

## Klavye kısayolları

| Tuş | İşlem |
|---|---|
| <kbd>←</kbd> <kbd>→</kbd> / <kbd>↑</kbd> <kbd>↓</kbd> / <kbd>PgUp</kbd> <kbd>PgDn</kbd> | Jog X / Y / Z |
| <kbd>Shift</kbd> | Hızlı jog (%100) |
| <kbd>Tab</kbd> · <kbd>M</kbd> | Jog ON/OFF · Cont/Step |
| <kbd>,</kbd> <kbd>.</kbd> · <kbd>[</kbd> <kbd>]</kbd> | Step değeri · Slow Jog % |
| <kbd>Ctrl</kbd>+<kbd>X</kbd>/<kbd>Y</kbd>/<kbd>Z</kbd> | O eksenin DRO'suna değer yaz |
| <kbd>F10</kbd> / <kbd>F11</kbd> | Besleme override azalt / artır |
| <kbd>Space</kbd> | Feed Hold |
| <kbd>Esc</kbd> | ACİL DURDUR |

---

## Simülasyon

```bash
python ornekler/ornek_uret.py
python main.py --simulasyon
```

Sanal tezgâh Mach3'ü **fiziksel olarak** taklit eder: eksen başına ivme ve
azami hız (trapez profil; Feed Hold/Stop yavaşlama rampasıdır), CV birleşmesi,
Reset (hareket/MDI/DRO yazma yok), G20/G21 ve LED'ler, iş/makine koordinatı,
program çalıştırma (geçerli satır, Run/Pause/Idle), ve Mach3'ün kendi
`JogOn`/`JogOff` motoru (X yön değeri **bilerek ters** kodlanmıştır; doğrulama
bunu öğrenmek zorundadır). Arayüzü, güvenlik kurallarını ve jog hissini
makineye dokunmadan denemek ve test etmek için kullanılır.

---

## Ayar dosyası

`~/.mach3_kontrol/ayarlar.json` (ilk çalıştırmada oluşur, eski sürümler
otomatik taşınır). Önemli bölümler:

```jsonc
{
  "jog": {
    "acik": true, "mod": "surekli",            // Jog ON/OFF, Cont | Step
    "yavas_yuzde": 20, "hizli_yuzde": 100,     // Slow Jog %, Shift
    "adim": 1.0, "adim_listesi": [10, 1, 0.1, 0.01, 0.001],
    "adim_kuyrugu": 5,                         // IncBuffer
    "surucu": "artimli",                       // | "mach3" (doğrulama gerekir)
    "tik_ms": 40, "ileri_sn": 0.3
  },
  "makine": { "hizli_hiz": {"X":8000,"Y":8000,"Z":3000},   // Motor Tuning Velocity (mm/dk)
              "ivme": {"X":400,"Y":400,"Z":250} },          // Motor Tuning Accel (mm/s²)
  "guvenlik": {
    "limitler_dogrulandi": false,              // ← makinenizi girince true yapın
    "yumusak_limit": {"X":{"min":0,"max":3000}, "Y":{"min":0,"max":1500}, "Z":{"min":-150,"max":0}},
    "max_jog_adim": 100, "max_jog_feed": 6000,
    "guvenli_z": 20, "makine_z_taban": null,
    "acil_reset": true, "birim_zorla_mm": true
  }
}
```

---

## Proje yapısı

```
mach3_kontrol/
├── kodlar.py         # OEM kod haritası (Mach3'ün kendi listelerinden türer)
├── baglanti.py       # COM: öncelikli kuyruk, toplu okuma, JogOn/JogOff
├── sanal_makine.py   # fiziksel Mach3 simülasyonu
├── jog.py            # Mach3 jog modeli + iki sürücü + yerel jog doğrulaması
├── denetleyici.py    # hepsini birleştiren beyin
├── guvenlik.py       # limitler, kalp atışı, kilit, günlük (arka plan yazıcı)
├── webapp.py         # bağımlılıksız web sunucusu + CSRF/DNS-rebinding koruması
├── web/              # index.html · app.css · mach3.css · app.js · ekran.js · home.js
│                     # onizleme.js (paylaşılan çizici) · onizleme.html + onizleme_pencere.js (ayrı pencere)
├── ayarlar.py  gcode.py  sure.py  kalibrasyon.py  is_takip.py  rapor.py
├── izleyici.py  klavye_kilidi.py  tani.py  cli.py
tests/  ornekler/
```

## Testler

```bash
pip install pytest
pytest                      # ~250 test: jog fiziği, güvenlik, web, kalibrasyon...
python main.py --tani       # bağlantı teşhisi (gerçek Mach3)
```

---

## Sınırlar

* Gerçek bağlantı için **Windows + Mach3 + 32-bit Python + pywin32** gerekir.
* **Fiziksel jog donanımda denenmedi** (yukarıdaki not); artımlı sürücünün
  Mach3 planlayıcısıyla davranışı (özellikle CV kipi kapalıysa) sahada
  doğrulanmalıdır. CV kapalıysa (G61) zincir ilk satırda `G64` ister.
* Mach3'ün Motor Tuning değerleri COM'dan okunamaz; hız/ivme ayarlarını elle
  girersiniz.
* Mach3'ün Jog Mode (Cont/Step/MPG) düğmesini kodla değiştirecek bir OEM kodu
  bilinmiyor; Mach3'ün **yerel** jog sürücüsü Mach3 Continuous kipindeyken
  çalışır, aksi halde artımlı sürücüye düşer. MPG desteklenmez.
* Tek eksen aynı anda jog edilir.
* Mach3'ün Steps/Backlash değerlerini program okuyup yazamaz (kalibrasyon).

## Lisans

MIT — bkz. [LICENSE](LICENSE).
