# Providence Baptist Church Smart Worship System (PSWS)

End-to-end worship assistant with **FastAPI + SQLite backend** and **React (CRA) frontend**.

## Features

- Live websocket stream at `/ws`
- Transcript ingestion at `POST /transcript` with scripture reference detection:
  - `Matthew 1 1`
  - `Matthew 1:1-3`
  - whole chapter `Matthew 1`
- Bible verse retrieval from `bible-api.com` with in-memory caching
- Verse broadcast one-by-one with small delay and DB logging
- Health endpoint: `GET /health`
- AI notes summary endpoint: `POST /notes/summary`
  - Uses OpenAI API if `OPENAI_API_KEY` exists
  - Falls back to offline heuristic summary
- QR access control:
  - `POST /admin/create_qr`
  - `GET /qr/{token}.png`
  - `GET /validate_qr?token=...`
  - Scan logging into SQLite
- Frontend projector/mobile + notes page:
  - Full-screen scripture presentation
  - Moving gradient background + watermark logo text
  - Word-by-word reveal animation
  - Arrow keys:
    - Left = previous verse
    - Right = next verse (or fetch reference)
  - SpeechRecognition start/stop controls
  - TTS reads incoming verse, cancelling previous speech
  - Connection status chip
  - Access denied UI for invalid/expired QR token

## Project Structure

- `backend/` FastAPI app + SQLite database
- `frontend/` CRA React app + plain CSS

## Backend Setup

PowerShell-friendly commands:

```powershell
cd backend
python -m pip install -r requirements.txt
python -m uvicorn app:app --host 0.0.0.0 --port 8000 --reload
```

If `uvicorn` command is blocked or not found, always use:

```powershell
python -m uvicorn app:app --host 0.0.0.0 --port 8000 --reload
```

## Frontend Setup

```powershell
cd frontend
npm install
npm start
```

- CRA is configured with `react-scripts`, so `npm start` launches dev server on port 3000.
- Optional API override:

```powershell
$env:REACT_APP_API_BASE="http://localhost:8000"
npm start
```

## Example API Calls

### Create a QR token

```powershell
Invoke-RestMethod -Method Post -Uri "http://localhost:8000/admin/create_qr" -ContentType "application/json" -Body '{"label":"Sunday Service","minutes_valid":120,"single_use":false}'
```

### Validate token

```powershell
Invoke-RestMethod "http://localhost:8000/validate_qr?token=YOUR_TOKEN"
```

### Send transcript chunk

```powershell
Invoke-RestMethod -Method Post -Uri "http://localhost:8000/transcript" -ContentType "application/json" -Body '{"text":"Please read Matthew 1 1 to 3","speaker":"Pastor John"}'
```

### Generate notes summary

```powershell
Invoke-RestMethod -Method Post -Uri "http://localhost:8000/notes/summary" -ContentType "application/json" -Body '{"text":"Service transcript text here..."}'
```

## Troubleshooting

- **`uvicorn` not recognized / blocked:**
  - Use `python -m uvicorn ...`
- **Frontend can’t connect to backend:**
  - Confirm backend is running on `0.0.0.0:8000`
  - Set `REACT_APP_API_BASE`
- **SpeechRecognition unavailable:**
  - Use Chrome/Edge and HTTPS/localhost context
- **OpenAI summary not used:**
  - Set `OPENAI_API_KEY`; fallback heuristic is automatic
- **Access denied in frontend:**
  - Ensure URL includes `?token=...`
  - Ensure token is unexpired / not consumed if single-use

## Notes

- No use of `eval()` anywhere in code.
- SQLite DB file is created at `backend/psws.db` on first run.
