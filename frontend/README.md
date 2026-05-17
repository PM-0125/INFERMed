# INFERMed Product Frontend

React + TypeScript + Vite frontend for the product-facing INFERMed interaction workspace.

## Run

```powershell
cd frontend
npm.cmd install
npm.cmd run dev -- --host 0.0.0.0 --port 5173
```

Open:

```text
http://localhost:5173
```

## Build And Lint

```powershell
npm.cmd run lint
npm.cmd run build
```

## Backend Wiring

The frontend currently uses sample data unless `VITE_INFERMED_API_URL` is set.

When the FastAPI backend exists, create `frontend/.env.local`:

```env
VITE_INFERMED_API_URL=http://localhost:8000
```

Expected API boundary:

- `POST /api/interactions/analyze`
- `POST /api/interactions/followup`

The UI is intentionally product-first:

- chip-based drug input, ready for future N-drug workflows
- AI assessment as the primary result
- evidence cards as side tabs
- PubChem/DrugBank-inspired medical visual language
