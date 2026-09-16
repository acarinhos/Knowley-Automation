# Knowley Soru Üretim Kontrolü

Knowley; Google Sheets'teki kategorizasyon algoritmasını, çoklu LLM fallback
akışını ve Firebase Firestore teslimatını tek bir üretim motorunda birleştirir.

## Hızlı başlangıç

```powershell
python -m pip install -r requirements.txt
python ui_server.py
```

Kontrol paneli varsayılan olarak `http://127.0.0.1:8765` adresinde açılır.
Tarayıcıyı otomatik açmadan başlatmak için:

```powershell
python ui_server.py --no-browser
```

Masaüstü arayüzü için `run_ui.bat` dosyasını açın veya `python ui_tkinter.py`
komutunu çalıştırın.

### Dış soru veri seti içe aktarma

Tkinter ekranının üstündeki **Dış Veri Seti** düğmesi JSON/CSV/TSV dosyalarını
veya doğrudan JSON/CSV URL'lerini içe aktarır. Jeopardy TSV şeması otomatik
tanınır; `answer` ipucu, `question` doğru yanıt olarak yorumlanır ve eksik üç
çeldirici aynı kategori havuzundaki diğer yanıtlardan deterministik üretilir.
Akış üç aşamalıdır:

1. **İçe Aktar:** Ham kayıtları okur; OpenTDB ve yaygın
   `question/options/answer` şemalarını tanır.
2. **Uygunluk Testi:** Soruyu Sheets taksonomisindeki kategori, alt kategori ve
   odak noktalarına eşler; uygun zorluk, süre, görsel tetikleyicisi ve
   `question_image_url: null` alanını sistem kurallarından tamamlar. Normal
   üretimle aynı kapsam/ülke planlayıcısını kullanır ve soru, seçenekler ile
   açıklamayı `tr`, `en`, `es`, `pt`, `de` dillerine tamamlar. Sonuç
   `data/imports/latest_external_import.json` dosyasına yazılır.
3. **Firebase'e Gönder:** Yalnızca kullanıcı onayından sonra uygun soruları
   ayrı `dbquestions` koleksiyonuna yollar; mevcut soru hash'lerini tekrar eklemez.

HTML konuşma bağlantıları yalnızca içeriklerinde gerçek bir JSON soru dizisi
varsa işlenir. Soru verisi içermeyen sayfalarda arayüz açıklayıcı bir hata verir.

Panelde üç üretim modu vardır:

- **154 kredi:** Ana kategori ve alt kategorileri 154 kredi sütunlarındaki ağırlıklara göre seçer.
- **64 kredi:** Aynı seçimi 64 kredilik kısa seri dağılımıyla yapar.
- **Temel:** Yalnızca Sheet'te `Temel Kategoriler` olarak işaretlenen alt hedeflerden üretir.

Seçilen soru adedi 154 veya 64 olmak zorunda değildir. Motor, istenen toplamı
Sheet'teki kredi oranlarına göre paylaştırır. Firestore'daki geçmiş kategori ve
alt kategori adetleri okunmaz; eski sorulara göre yakalama/dengeleme yapılmaz.

Kategori ve alt kategori kullanıcıdan istenmez. Ülke profili varsayılan olarak
kapalıdır; açılırsa yalnızca ayrı Sheet profili bulunan Türkiye, ABD, Çin,
Japonya veya Almanya seçilebilir. Süre her zaman otomatik hesaplanır.

## Ortam değişkenleri

Mevcut `.env` dosyasında en az bir LLM anahtarı ile Firebase servis hesabı
tanımlı olmalıdır:

```dotenv
GEMINI_API_KEY=...
GROQ_API_KEY=...
COHERE_API_KEY=...
NVIDIA_API_KEY=...
FIREBASE_CREDENTIALS_PATH=knowley-1-firebase-adminsdk.json
FIREBASE_STORAGE_BUCKET=knowley-1-categories
```

Hesabınıza özel quota limitlerini biliyorsanız paneldeki tahmini kalan soru
sayısını özelleştirebilirsiniz:

```dotenv
GEMINI_RPM=
GEMINI_RPD=
GEMINI_TPM=
GROQ_RPM=30
GROQ_RPD=1000
GROQ_TPM=8000
COHERE_RPM=20
COHERE_MONTHLY_REQUESTS=1000
```

Groq gerçek kalan istek başlıklarını bir üretim çağrısından sonra döndürürse
panel tahmin yerine bu kesin değeri gösterir. Gemini'nin hesaba özgü kesin
limitleri AI Studio'da görüntülenir; API ortak bir kalan-quota uç noktası
sunmadığı için panel yalnızca tanımlı limit ve yerel oturum kullanımını gösterir.

## Sheets algoritmasını yenileme

`data/knowley_algorithm_snapshot.json`, kaynak çalışma kitabının doğrulanmış
v1.0.5 snapshot'ıdır. Üretim sırasında ağ kesintisi olsa bile motor bu veriyle
çalışır. Snapshot'ı güncellemek için çalışma sayfasını `.env` dosyasındaki
servis hesabının `client_email` adresiyle okuyucu olarak paylaşın ve çalıştırın:

```powershell
python scripts/sync_algorithm_sheet.py
```

İsterseniz Firebase'den farklı bir salt-okunur servis hesabı kullanabilirsiniz:

```dotenv
GOOGLE_SHEETS_CREDENTIALS_PATH=path/to/sheets-reader.json
KNOWLEY_ALGORITHM_SHEET_ID=1LI36wvw4TIWHBHAPgJ2lWyWz6nkj_Hmwp3eN83aD8rs
```

## Worker

Eski sonsuz worker davranışı korunur ve artık Sheets kredi havuzunu kullanır:

```powershell
python worker.py
```

Her başarılı soru; hash tabanlı tekrar kontrolü, otomatik süre, zorluk, dil,
kategori, GL/LO ve algoritma kaynağı metadatası eklendikten sonra Firestore'daki
`questions` koleksiyonuna **1.1.0** şemasıyla yazılır. Her dilde soru kökü en
fazla 150 karakter ve 25 kelime olabilir; sınırı aşan çıktı kaydedilmez ve
otomatik yeniden üretim akışına girer.

Otomatik zorluk üretiminde her 10 soruluk `4 Kolay / 4 Orta / 2 Zor` bloktan
rastgele 3 Kolay ve 1 Orta soru ayrıca işaretlenir. Seçilen Kolay soruların
`starting_difficulty` değerleri geliş sırasıyla `Kolay`, `Orta`, `Zor`; seçilen
Orta sorunun değeri `Zor` olur. Bu alan diğer altı soruda bulunmaz ve normal
zorluk, puan, süre veya üretim dengelerini etkilemez.

Tkinter Firebase kartındaki **1.1.0+ üretim tablosu**, `questions`
koleksiyonunu salt okunur tarar ve bugün, bu hafta, bu ay ve tüm zamanlar için
kategori/alt kategori adetlerini gösterir. Ana Firebase sayacı tüm belgeleri;
denetim tablosu yalnızca `version` veya `dbVersion >= 1.1.0` belgelerini kapsar.

LLM havuzu başarısız sağlayıcıları geçici cooldown'a alır. NVIDIA timeout'u 20
saniyedir; Groq `413 Payload Too Large` verdiğinde aynı payload diğer Groq
modellerine tekrar gönderilmez. Ayrıntılı kategori kılavuzu sistem mesajında
tek kez taşınarak istek boyutu küçültülür.

## Test

```powershell
python -m unittest discover -s tests -v
```
