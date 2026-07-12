# Crypto Scalping Scanner - Deployment Guide (Render)

## 🚀 Deployment auf Render.com

### Voraussetzungen:
1. **GitHub Account** (kostenlos)
2. **Render Account** (kostenlos): https://render.com
3. **MongoDB Atlas Account** (kostenlos): https://www.mongodb.com/cloud/atlas

---

## 📋 Schritt 1: MongoDB Atlas Setup (5 Minuten)

1. Gehe zu: https://www.mongodb.com/cloud/atlas/register
2. Registriere dich (kostenlos)
3. **Create a Cluster** → "M0 Free" auswählen
4. **Region:** Frankfurt (nächste Region für dich)
5. **Cluster Name:** `crypto-scanner`
6. Warte 3-5 Minuten bis Cluster ready ist

### Datenbank User erstellen:
1. Links: **Database Access**
2. **Add New Database User**
3. Username: `scanner` / Password: **Generiere sicheres Passwort** (SPEICHERN!)
4. **Read and write to any database**
5. **Add User**

### Network Access konfigurieren:
1. Links: **Network Access**
2. **Add IP Address**
3. **Allow Access from Anywhere** (0.0.0.0/0)
4. **Confirm**

### Connection String kopieren:
1. Links: **Database** → **Connect**
2. **Connect your application** → **Python**
3. Kopiere den String, z.B.:
   ```
   mongodb+srv://scanner:<password>@crypto-scanner.xxxxx.mongodb.net/?retryWrites=true&w=majority
   ```
4. **Ersetze `<password>`** mit deinem generierten Passwort
5. **SPEICHERN** - das ist deine `MONGO_URL`

---

## 📋 Schritt 2: Code auf GitHub pushen

### GitHub Repository erstellen:
1. Gehe zu: https://github.com/new
2. **Repository Name:** `crypto-scanner`
3. **Private** (damit deine API Keys sicher sind)
4. **Create Repository**

### Code hochladen (im Terminal):
```bash
cd /app
git init
git add .
git commit -m "Initial commit - Crypto Scalping Scanner"
git branch -M main
git remote add origin https://github.com/DEIN_USERNAME/crypto-scanner.git
git push -u origin main
```

**Alternative:** Zip Download aus Emergent → auf GitHub manuell hochladen

---

## 📋 Schritt 3: Backend auf Render deployen

1. Gehe zu: https://dashboard.render.com
2. **New +** → **Web Service**
3. **Connect GitHub** → dein Repository auswählen
4. **Konfiguration:**
   - **Name:** `crypto-scanner-backend`
   - **Region:** Frankfurt
   - **Branch:** main
   - **Root Directory:** `backend`
   - **Runtime:** Python 3
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `uvicorn server:app --host 0.0.0.0 --port $PORT`
   - **Instance Type:** Free

5. **Environment Variables** hinzufügen (unter "Advanced"):

   | Key | Value |
   |-----|-------|
   | `MONGO_URL` | (dein MongoDB Atlas String) |
   | `DB_NAME` | `crypto_scanner` |
   | `TELEGRAM_BOT_TOKEN` | `8794534378:AAFxBdiYWxoj3yhwwsvXSX1t9jSuevv-_Tk` |
   | `TELEGRAM_CHAT_ID` | `8602936088` |
   | `BITUNIX_API_KEY` | `9acf71135150d8046fc14e1493e16f8f` |
   | `BITUNIX_SECRET_KEY` | `9ee6fd2bb3c31af3a5ea092df4307a8a` |
   | `PYTHON_VERSION` | `3.11.0` |

6. **Create Web Service** → Warte 3-5 Minuten

7. **Kopiere die Backend URL**, z.B.: `https://crypto-scanner-backend.onrender.com`

---

## 📋 Schritt 4: Frontend auf Render deployen

1. Render Dashboard → **New +** → **Static Site**
2. Dein Repository auswählen
3. **Konfiguration:**
   - **Name:** `crypto-scanner-frontend`
   - **Branch:** main
   - **Root Directory:** `frontend`
   - **Build Command:** `yarn install && yarn build`
   - **Publish Directory:** `build`

4. **Environment Variables:**

   | Key | Value |
   |-----|-------|
   | `REACT_APP_BACKEND_URL` | Deine Backend URL aus Schritt 3 |

5. **Advanced** → **Rewrites and Redirects:**
   - Source: `/*`
   - Destination: `/index.html`
   - Action: Rewrite

6. **Create Static Site** → Warte 3-5 Minuten

---

## 📋 Schritt 5: Testen!

1. Öffne deine Frontend URL, z.B.: `https://crypto-scanner-frontend.onrender.com`
2. Warte kurz bis Backend startet (Free tier hat cold start ~30 Sekunden)
3. Du solltest die App sehen mit Live-Daten!

---

## ⚠️ Wichtige Hinweise:

### Free Tier Limitations:
- **Backend geht in Sleep** nach 15 Minuten Inaktivität
- **Cold Start:** ~30 Sekunden beim ersten Request
- **Aber:** WebSocket zu Bitunix bleibt aktiv wenn Backend läuft

### Für 24/7 Betrieb:

**Option 1: Cronjob-Ping** (kostenlos)
- Nutze https://cron-job.org (kostenlos)
- Ping deine Backend URL alle 10 Minuten: `https://crypto-scanner-backend.onrender.com/`
- Verhindert Sleep

**Option 2: Render Paid Plan**
- $7/Monat für immer aktiv
- Kein Cold Start
- Bessere Performance
- **Empfohlen für ernsthaftes Trading!**

### Bitunix WebSocket:
- Läuft **ausgehend** vom Server → keine Probleme
- Reconnect ist im Code implementiert
- Bei Render Restart automatische Neuverbindung

---

## 🔧 Troubleshooting

### Backend startet nicht:
- Logs checken: Render Dashboard → Service → Logs
- Meist: Falsche `MONGO_URL` → Passwort URL-encoden!
- Sonderzeichen: `@` → `%40`, `#` → `%23`

### Frontend zeigt keine Daten:
- Browser DevTools → Console → Fehler prüfen
- Backend URL richtig gesetzt? (mit `https://` und OHNE trailing slash)
- CORS Fehler? → Backend `.env` → `CORS_ORIGINS="*"`

### WebSocket verbindet nicht:
- WebSocket URL wird automatisch aus `REACT_APP_BACKEND_URL` gebaut
- Muss mit `https://` starten (wird zu `wss://` konvertiert)

### Telegram sendet keine Nachrichten:
- Environment Variables auf Render checken
- Bot Token korrekt? Chat ID korrekt?
- Render Logs: `Failed to send Telegram notification`

---

## 💰 Kostenübersicht:

| Service | Kostenlos | Empfohlen (Paid) |
|---------|-----------|------------------|
| Render Backend | Free (mit Sleep) | $7/Monat |
| Render Frontend | Free | Free |
| MongoDB Atlas | Free (512 MB) | Free reicht |
| Bitunix API | Kostenlos | Kostenlos |
| Telegram Bot | Kostenlos | Kostenlos |
| **TOTAL** | **0€/Monat** | **~7€/Monat** |
