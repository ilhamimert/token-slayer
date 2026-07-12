# Token Slayer

**Claude Code token kullanımını azalt.** Token Slayer kod tabanını tamamen yerel olarak analiz eder — dosya yapısı, bağımlılıklar, git geçmişi, ölü kod, token dağılımı — ve Claude Code'a projenin tamamı yerine sadece ihtiyaç duyduğu bağlamı verir.

🇬🇧 [English README](README.md)

> **API key yok. Abonelik yok. Faturalandırma yok.** Token Slayer hiçbir LLM'i kendisi çağırmaz — tamamen makinende çalışan, yerel bir statik-analiz aracıdır. Zaten sahip olduğun Claude Code, [MCP](https://modelcontextprotocol.io/) üzerinden *onu* çağırır, tersi değil.

---

## İçindekiler

- [Ne Yapar](#ne-yapar)
- [Kurulum](#kurulum)
- [Hızlı Başlangıç](#hızlı-başlangıç)
- [Komut Referansı](#komut-referansı)
- [MCP Araçları (Claude Code için)](#mcp-araçları-claude-code-için)
- [Nasıl Çalışır](#nasıl-çalışır)
- [Sık Sorulan Sorular](#sık-sorulan-sorular)
- [Geliştirme](#geliştirme)
- [Lisans](#lisans)

---

## Ne Yapar

Claude Code, sadece görevle ilgili dosyaları okuduğunda en iyi performansı gösterir. Kendi haline bırakılırsa genelde fazla okur — bütün dosyalar, bütün dizinler — hiç ihtiyaç duymadığı içerik için token harcar. Token Slayer bu boşluğu kapatır:

| Sorun | Token Slayer'ın çözümü |
|---|---|
| Claude, projeyi anlamak için hepsini okuyor | `snapshot` — sıkıştırılmış bir `CONTEXT.md` (dosya ağacı + fonksiyon/sınıf imzaları, gövde yok) |
| Claude, o anki görevle alakasız dosyaları okuyor | `focus` — dosyaları göreve alaka düzeyine göre sıralar; `--with-deps` ile import grafiği de eklenir |
| Küçük bir değişiklikten sonra Claude yine bütün dosyayı okuyor | `diff-context` — git'ten sadece değişen satır aralıklarını (+ padding) döndürür |
| `.claudeignore`'un işe yarayıp yaramadığını bilmiyorsun | `slim` / `tokens` — `.claudeignore` öncesi/sonrası token bütçe analizi |
| Claude, syntax hatası olan bir dosyada token harcıyor | `audit` — koda geçmeden önce syntax hatalarını, circular import'ları, eskimiş dokümanları işaretler |
| `CLAUDE.md`'in güncelliğini yitiriyor | `generate-config` — taze bir analizden yeniden üretir |
| "Bu proje token açısından optimize mi?" sorusuna tek bir skor istiyorsun | `score` — 0-100 arası kompozit sağlık skoru |

Her şey **yerel** çalışır. Kendi git deponun dışında hiçbir ağ isteği atılmaz.

## Kurulum

### Seçenek A — pipx ile global kurulum (Önerilen)

Bir kere kurulduktan sonra, herhangi bir projeden, herhangi bir makineden çalışır:

```bash
pip install pipx
pipx install "token-slayer[mcp] @ git+https://github.com/ilhamimert/token-slayer.git"
```

### Seçenek B — Yerel geliştirme kurulumu

```bash
git clone https://github.com/ilhamimert/token-slayer.git
cd token-slayer

# Windows
.\setup.ps1

# macOS / Linux
./setup.sh
```

Her iki setup betiği de bir `.venv` oluşturur, tüm bağımlılıkları kurar ve — `pipx` mevcutsa — `tslayer`'ı ayrıca global olarak kurar, böylece sonrasında herhangi bir proje dizininden çalışır.

## Hızlı Başlangıç

```bash
cd proje/yolun
tslayer init                 # .mcp.json yazar — tek komut, herhangi bir proje
```

Ardından, projende Claude Code penceresini yeniden yükle (VS Code'da `Ctrl+Shift+P` → `Developer: Reload Window`) ki yeni MCP sunucusunu algılasın. Bundan sonra Claude Code, o projede çalışırken Token Slayer'ın tüm araçlarına otomatik olarak erişir.

CLI'ı doğrudan da deneyebilirsin:

```bash
tslayer score .              # kompozit sağlık skoru (0-100)
tslayer audit .              # syntax hataları, circular import'lar, eskimiş CLAUDE.md
tslayer focus . "Redis cache ekle" --with-deps
```

## Komut Referansı

| Komut | Ne yapar |
|---|---|
| `tslayer init [yol]` | Claude Code'un bu projede Token Slayer'ı otomatik yüklemesi için `.mcp.json` yazar. |
| `tslayer analyze <yol>` | Dosya istatistikleri, bağımlılık grafiği, sık değişen dosyalar. Bayraklar: `--quality`, `--cycles`, `--dead-code`, `--tokens`, `--chart`, `--multilang`, `--json`. |
| `tslayer score <yol>` | Kompozit 0-100 sağlık skoru (token tasarrufu, type coverage, complexity, ölü kod, circular import). |
| `tslayer audit <yol>` | CI-dostu kontrol: `CLAUDE.md` güncel mi? Syntax hatası var mı? Circular import var mı? Token bütçesi çok mu büyük? Başarısızlıkta sıfır olmayan exit code döner. |
| `tslayer generate-config <yol>` | Optimize edilmiş bir `CLAUDE.md` üretir (proje özeti, sık değişen dosyalar, ölü kod, önerilen `.claudeignore`). |
| `tslayer tokens <yol>` | `.claudeignore` öncesi/sonrası görsel grafikle token bütçe raporu. |
| `tslayer snapshot <yol>` | `CONTEXT.md` üretir — dosya ağacı + fonksiyon/sınıf imzaları, gövde yok. Genelde tüm dosyaları okumaktan %80-90 daha küçük. |
| `tslayer focus <yol> "<görev>"` | Dosyaları görev tanımına alaka düzeyine göre sıralar. `--with-deps` her dosyanın doğrudan import komşularını ekler. |
| `tslayer diff-context <yol>` | Git'ten değişen dosyaları + değişen satır aralıklarını (padding ile) döndürür — bütün dosya değil, sadece değişeni oku. `--staged`, `--pad N`. |
| `tslayer slim <yol>` | Token bütçesine ulaşmak için `.claudeignore` desenleri önerir. `--apply` ile yazar. |
| `tslayer sessions` | Aktif Claude Code oturumlarının canlı token kullanımı. Canlı yenilenen görünüm için `--watch`. |
| `tslayer checkpoint <yol>` | Mevcut ilerlemeyi yeni bir konuşma için taze-başlangıç promptuna (`CHECKPOINT.md`) sıkıştırır. |
| `tslayer decision "<metin>"` | `DECISIONS.md`'ye bir mimari karar kaydeder, böylece gelecekteki Claude oturumları kodun *neden* böyle yapılandırıldığını bilir. |
| `tslayer init-hooks <yol>` | Her commit'ten önce `tslayer audit` çalıştıran bir git pre-commit hook'u kurar. |
| `tslayer mcp` | MCP stdio sunucusunu başlatır (bunu `.mcp.json` otomatik başlatır — elle çalıştırman gerekmez). |

Çoğu komut makine-okunabilir çıktı için `--json` destekler.

## MCP Araçları (Claude Code için)

`.mcp.json` kaydedildikten sonra (`tslayer init` ile), Claude Code bunları doğrudan çağırabilir:

| Araç | Amacı |
|---|---|
| `snapshot_tool` | Sıkıştırılmış proje özeti — herhangi bir dosya okumadan önce ilk bunu çağır. |
| `decisions_tool` | Kayıtlı mimari kararları oku — herhangi bir yapıyı değiştirmeden önce çağır. |
| `focus_tool` | Göreve alakalı dosya sıralaması, isteğe bağlı import-grafiği bağlamıyla (`with_deps`). |
| `diff_context_tool` | Git'ten değişen satır aralıkları. |
| `syntax_check_tool` | Claude düzenlemeden önce tespit edilen syntax hatalı dosyalar. |
| `health_score_tool` | Kompozit proje sağlık skoru. |
| `analyze_project_tool` | Dosya/fonksiyon/sınıf sayıları, complexity, tespit edilen framework'ler. |
| `count_tokens_tool` | `.claudeignore` öncesi/sonrası token sayıları. |
| `find_cycles_tool` | Circular import bağımlılıkları. |
| `most_imported_tool` | En yüksek etkili (en çok import edilen) dosyalar. |
| `generate_config_tool` | Talep üzerine `CLAUDE.md` üretir. |

## Nasıl Çalışır

- **Parsing**: her `.py` dosyası [tree-sitter](https://tree-sitter.github.io/) ile parse edilir — import'lar, fonksiyonlar, sınıflar, cyclomatic complexity ve syntax hataları, hepsi gerçek bir AST'den (regex değil).
- **Bağımlılık grafiği**: çözümlenmiş import'lardan [networkx](https://networkx.org/) ile kurulur, circular-dependency tespiti ve etki analizi için kullanılır.
- **Sağlık skoru**: ağırlıklı bir kompozit —

  | Bileşen | Ağırlık |
  |---|---|
  | Token tasarrufu (`.claudeignore` etkinliği) | %30 |
  | Type coverage (test-dışı fonksiyonlar) | %25 |
  | Complexity (fonksiyon başına dallanma yoğunluğu) | %20 |
  | Ölü kod (kullanılmayan export'lar) | %15 |
  | Circular dependency | %10 |

- **Cache**: parse edilen dosya verisi cache'lenir (`.cca_cache.json`) ve mtime/boyuta göre geçersiz kılınır, böylece büyük bir projede tekrarlanan çalıştırmalar hızlı olur.

## Sık Sorulan Sorular

**API key veya abonelik gerekiyor mu?**
Hayır. Token Slayer hiçbir LLM'i çağırmaz — yerel bir statik-analiz aracıdır. Zaten sahip olduğun Claude Code, *onu* çağırır, tersi değil.

**Yeni bir makinede / başka biri için çalışır mı?**
Evet — makine başına bir kere `pipx install "token-slayer[mcp] @ git+https://github.com/ilhamimert/token-slayer.git"`, proje başına bir kere `tslayer init`. [Kurulum](#kurulum) bölümüne bak.

**Hangi dilleri analiz ediyor?**
Python için tam destek. TypeScript ve Go için `analyze --multilang` ile temel (regex tabanlı) destek.

**Proje-özel veriyi nerede saklıyor?**
`.cca_cache.json` (parse cache'i), `CLAUDE.md`, `CONTEXT.md`, `CHECKPOINT.md`, `DECISIONS.md` — hepsi proje kökünde düz dosyalar, istediğin gibi commit edebilir ya da `.gitignore`'a ekleyebilirsin.

## Geliştirme

```bash
git clone https://github.com/ilhamimert/token-slayer.git
cd token-slayer
python -m venv .venv
.venv/Scripts/pip install -e ".[dev,mcp]"   # Windows
# .venv/bin/pip install -e ".[dev,mcp]"     # macOS/Linux

pytest -q
```

## Lisans

Apache License 2.0 — bkz. [LICENSE](LICENSE). Telif hakkı © 2026 [ilhamimert](https://github.com/ilhamimert).
