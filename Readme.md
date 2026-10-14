# New York OCR

Frontend and backend for local use.

## Access on localhost

1. Backend (API on port 7778):

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

2. Frontend (app on port 5173). API calls to `/api` are proxied to `http://127.0.0.1:7778`:

```bash
cd frontend
npm ci
npm run dev
```

Open [http://localhost:5173](http://localhost:5173). The app redirects to the login page.
