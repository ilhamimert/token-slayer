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
