# PRD – Krypto Alert / Daytrading-Website (Fork von NEW25.07)

## Original-Problemstellung (26.06.2026 / Session 26.07.2026)
Bestehende, produktiv laufende Daytrading-Website (Repo dean06greif-ai/Krypto_Alert, Branch NEW25.07).
Lokale Backtests / Strat-Optimierer / Strat-Finder verbessern – sauber, modular, rückwärtskompatibel,
sehr customizable. Konkret gefordert:
1. Walk-Forward-Modus als zusätzliche Einstellung bei Strat Finder / Optimierer / Kombi (Default 75% Training / 25% Test),
   Bewertung bevorzugt Strategien mit ähnlich guter Performance auf Training UND Test (Overfitting-Schutz, WF-Score).
2. Drawdown-Filter: max. Drawdown relativ zum PnL (Default 40%), gilt für Finder, Optimierer und Walk-Forward.
3. Konstanz-Test: Zeitraum in Abschnitte teilen (einstellbar, Default 30 Tage), max. Abweichung einstellbar (Default 20%),
   zu schwankende Strategien aussortieren.
4. Immer Top-5-Ergebnisse anzeigen, User wählt aus, welche Strategie übernommen wird.
5. GPU-Unterstützung für Local Mode (NVIDIA, Auto-Erkennung, CPU-Fallback).
6. Zeitraum-Auswahl in 360-Tage-Schritten bis 15 Jahre (5400 Tage) erweitern.

## Architektur
- Backend: FastAPI (/app/backend), MongoDB (Motor), Router + Services + Strategies.
- Frontend: React CRA/craco (/app/frontend), deutschsprachige UI, Phosphor-Icons.
- Local Worker: /app/local_worker/worker.py – Outbound-Polling, nutzt identischen services/-Code.
- Auth: JWT, Admin über backend/.env (ADMIN_USER=Admin, ADMIN_PASSWORD=admin).
- Echte Marktdaten (Bitunix), kein Mock.

### Neue Module (26.07.2026)
- `backend/services/robustness.py`: parse_config, split_histories, walk_forward_eval (WF-Score,
  Konsistenz = min/max der zeitnormierten Qualität, Score = Mittel × (0.4+0.6×Konsistenz), negativ wenn
  eine Seite verliert), dd_check (DD/PnL-Ratio, PnL<=0 fällt durch), collect_chunk_pnls + evaluate_chunks
  (Konstanz: std/mean der Abschnitts-PnLs in %), TopTracker (dedupe per rule_key).
- `backend/services/gpu_accel.py`: CuPy-Erkennung (USE_GPU=1 + cupy), GPU-Kernels für rolling
  mean/std/max/min mit CPU-Fallback (pandas-identisch). Genutzt von fast_sim (SMA, Bollinger, Stochastik).
  EMA/RSI/MACD + Trade-Simulation bleiben bewusst CPU (rekursiv/ereignisbasiert).

### Integration (services/optimizer.py)
- Body-Felder: walk_forward{enabled,train_pct}, dd_filter{enabled,max_dd_pct}, constancy{enabled,chunk_days,max_deviation_pct}.
- WF-Split VOR fs_map/Prozess-Pool → gesamte Suche läuft auf Trainingsdaten.
- _score(..., dd_max_pct): DD-Verletzer bekommen -5e8-Malus (opt-in).
- TopTracker wird in _discover/_refine/_optimize_trade_settings gefüllt; params-Modus nutzt die top-Liste.
- _finalize_top5: Top-~10 Kandidaten → Test-Evaluierung (WF), DD-Check (Training UND Test), Konstanz-Test,
  Re-Ranking (bestanden zuerst; bei WF nach wf_score, sonst score), Fallback = bestes Suchergebnis.
- result: top5[], walk_forward{train_days,test_days,train_pct}, robustness{Config-Echo}. Alles additiv/rückwärtskompatibel.
- days-Clamp 1500 → 5500 (auch routers/backtest.py, routers/local_worker.py).

### Frontend (Optimizer.js)
- Sektion "ROBUSTHEIT & WALK-FORWARD" (Toggles opt-wf-toggle/opt-dd-toggle/opt-ct-toggle + Eingaben
  opt-wf-trainpct/opt-dd-maxpct/opt-ct-chunkdays/opt-ct-maxdev, Split-Info opt-wf-split-info), persistiert in localStorage.
- Top-5-Karten (opt-top5, opt-top5-0..4) mit WF-Score/Konsistenz, DD/PnL-Badge, Konstanz-Badge,
  Training-/Test-Metriken, Regeln/Parameter-Pills; Klick wählt aus, Übernehmen/Speichern nutzt selEntry.
- DAY_OPTIONS bis 5400 (auch Backtester.js, LocalWorkerPanel DL_DAYS).
- LocalWorkerPanel: use_gpu-Select aktiviert, GPU-Status im Worker-Header (aktiv/aus).
- Worker v1.2.0: gpu_info via gpu_accel/CuPy (torch-Fallback), USE_GPU aus Website-Einstellung.

## Was wurde umgesetzt (26.07.2026)
- [x] Repo geklont, Umgebung eingerichtet (.env neu erstellt – waren nicht im Repo), Services laufen.
- [x] Features 1–6 komplett (siehe oben), alles optional & rückwärtskompatibel.
- [x] Unit-/Regressionstests: backend/tests/test_robustness_features.py (20 Tests) + Testing-Agent-Suite
      tests/test_iter11_robustness.py (6 Tests) – alle grün.
- [x] E2E verifiziert (curl + Playwright): Discovery ohne/mit WF+DD+Konstanz liefert Top-5, UI-Auswahl funktioniert.
- [x] Bugfixes nach Testing-Agent: tracker an _discover übergeben, Top-5-Fallback wenn alle Kandidaten
      unter Min-Trades, Filter-Verletzer werden angezeigt & geflaggt statt versteckt.
- [x] Doku: local_worker/README.md GPU-Abschnitt, requirements-Hinweis (cupy-cuda12x/11x).

## Bekannte Punkte / Nicht-Regressionen
- tests/test_winrate_bug.py::test_winrate_bugfix_full_flow erwartet "Re-hydrated"-Logzeile – schlägt in
  frischer Umgebung ohne persistierte Trades fehl (Alt-Test, umgebungsabhängig, keine Code-Regression).
- Kosmetisch (vorbestehend, Iter10): React-Warnung <span> in <option> im opt-days-Select.
- GET /api/localworker/settings liefert {settings:{...}} (vorbestehendes Format).
- GPU-Wirkung konnte im Pod nicht real gemessen werden (keine NVIDIA-GPU) – CPU-Fallback getestet
  (Ergebnisse identisch zu pandas). Realistischer Nutzen: Indikator-Vorberechnung bei großen Zeiträumen;
  Multi-Core (SIM_WORKERS) bleibt der größte Hebel.

## Backlog / Nächste Schritte
- P1: Rolling Walk-Forward (mehrere Train/Test-Fenster statt einem Split) als Erweiterung.
- P1: Top-5 auch für lokalen Worker-Pfad end-to-end mit echtem Worker verifizieren (Code identisch, Worker nutzt gleiche services/).
- P2: WF-/Konstanz-Ergebnisse in Optimizer-Historie (optimizer_runs) visualisieren (Verlauf über mehrere Läufe).
- P2: GPU-Beschleunigung für Batch-Regelauswertung (viele Kandidaten gleichzeitig auf GPU) evaluieren.
- P2: Kosmetik: <option>-Warnung beheben; localworker/settings-Format vereinheitlichen.
