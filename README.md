# Ödeme Takip

Özel ders velilerinin ödemelerini takip etmek, asistan komisyonunu hesaplamak ve haftalık ders programı hazırlamak için küçük, kendi sunucunuzda çalışan bir web uygulaması.

**Teknoloji:** Python 3 · Flask · SQLite · saf JavaScript (derleme adımı yok, Docker gerekmez)

## Özellikler

- **Giriş zorunlu** — oturum açmadan hiçbir sayfa görünmez; kaba kuvvet denemelerine karşı kilit, CSRF koruması.
- **Veliler ve öğrenciler** — bir veliye birden fazla çocuk; her çocuğa renk ve kendi tarifesi (online / yüz yüze ders başı ücret).
- **Ödemeler** — ders sayısı girilince tutar tarifeden otomatik hesaplanır (elle değiştirilebilir), son ödeme tarihi, gecikme takibi.
- **SMS hatırlatma** — tek tıkla hazır mesaj: kopyala, telefonun SMS uygulamasında veya WhatsApp'ta aç. Taslaklar Ayarlar'dan düzenlenir
  (`{veli}`, `{ogrenci}`, `{tutar}`, `{ders_sayisi}`, `{ders_detay}`, `{son_odeme}`, `{aciklama}`).
- **Dekont yükleme** — ödeme tamamlanırken resim/PDF dekont eklenir; sadece giriş yapmış kullanıcılar görebilir.
- **Asistan komisyonu** — varsayılan olarak yüzde veya ders başı sabit tutar; öğrenciye özel komisyon tanımlanabilir.
  Komisyon sayfasında kazanılan, size ödenen ve kalan bakiye; size ödeme yapıldığında bakiyeden düşülür.
- **Haftalık program** — öğrenciyi sürükleyip saate bırakın (telefonda dokun-dokun), günlük doluluk oranı ve müsait günler, **PNG olarak indir**.
- **Haftalık yedek** — her pazar 03:00'te veritabanı yedeği; toplam 100 MB'ı aşınca en eski yedekler silinir. Ayarlar'dan elle yedek alınıp indirilebilir.

## Yerelde çalıştırma

```bash
python -m venv .venv
.venv/Scripts/activate            # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
python manage.py create-user asim --name "Asım"
python manage.py run              # http://127.0.0.1:5000
```

Testler: `python -m unittest discover tests`

## Sunucuya kurulum (Ubuntu, Docker'sız)

```bash
# Kodu /opt/odemetakip altına koyun (git clone veya kopyalama), sonra:
sudo bash /opt/odemetakip/deploy/install.sh
cd /opt/odemetakip && sudo -u odemetakip .venv/bin/python manage.py create-user asim --name "Asım"
```

`install.sh` şunları yapar: `odemetakip` sistem kullanıcısı, `/var/lib/odemetakip` veri klasörü, rastgele `SECRET_KEY` içeren `.env`,
sanal ortam, `odemetakip.service` (gunicorn, `127.0.0.1:8098`) ve `odemetakip-backup.timer` (haftalık yedek).

Uygulama sadece `127.0.0.1:8098` adresini dinler; dışarıya Cloudflare Tunnel (veya nginx) ile açılır. Cloudflare Tunnel örneği:

```yaml
ingress:
  - hostname: odeme.ornek.com
    service: http://127.0.0.1:8098
```

### Güncelleme

```bash
cd /opt/odemetakip && sudo git pull && sudo bash deploy/install.sh
```

### Yönetim komutları

| Komut | Açıklama |
|---|---|
| `manage.py create-user <ad> [--name "Ad Soyad"]` | Yeni kullanıcı |
| `manage.py set-password <ad>` | Şifre sıfırla |
| `manage.py backup` | Elle yedek al |

## Veriler

Veritabanı (`odeme.db`), dekontlar (`uploads/`) ve yedekler (`backups/`) `DATA_DIR` klasöründe tutulur ve **depoya dahil edilmez**.
Yedekten geri dönmek için: servisi durdurun, `gunzip -c backups/odeme-....db.gz > odeme.db`, servisi başlatın.
