# Architecture Decisions

> Managed by `tslayer decision`.
> Claude reads this to understand WHY the code is structured this way.
> Never remove entries — add a new one if a decision changes.

### 2026-07-10 [architecture]
complexity per-function hesaplaniyor — per-file buyuk dosyalari haksiz penalize ediyor

### 2026-07-10 [bugfix]
dead code tespit @property, @staticmethod, @app.command dekorasyon kontrolu yapiyor — framework entry point'leri false positive oluyordu

### 2026-07-10 [architecture]
test dosyalari type coverage ve dead code metriklerinden harici tutuluyor — pytest fonksiyonlarinin return type annotation'i olmuyor

### 2026-07-13 [refactor]
cli.py analyze()/audit() kucuk yardimci fonksiyonlara bolundu - complexity_score 67.5->70.2, health total 74.4->75.2 (C->B). Saf extraction, davranis degismedi.

### 2026-07-13 [architecture]
Faz A tamamlandi: 4 kucuk ozellik eklendi - syntax_check_tool (has_syntax_error alani + audit entegrasyonu), rank_files_with_context (focus+graph birlesimi, --with-deps flag), diff_context.py (git diff satir-seviyesi context, yeni diff-context komutu), snapshot minify (_strip_boilerplate, yorum/bos satir temizligi). 301 test geciyor (251 -> 301). Proxy katmanina (kasitli silinmis) dokunulmadi.

### 2026-07-13 [bugfix]
setup.ps1/setup.sh duzeltildi: (1) yanlis 'API key ekle' uyarisi kaldirildi (proxy zaten silinmisti, .env.example zaten API key gerekmedigini soyluyordu ama script hala eski uyariyi veriyordu), (2) pipx ile global kurulum eklendi - varsa .mcp.json 'tslayer' komutunu kullaniyor (makineden bagimsiz, baska projelere/kisilere kopyalanabilir), yoksa eski venv-tam-yol davranisina geri donuyor.
