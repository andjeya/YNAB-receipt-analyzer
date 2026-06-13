# Frontend (Next.js)

Mobile-first UI for receipt review and YNAB sync.

## Pages

- `/` receipt list with status focus and timing summary
- `/receipts/[id]` receipt detail editor + preview + bottom action bar

## Run

```bash
npm install
npm run dev
```

Set API base URL via `NEXT_PUBLIC_API_BASE_URL` (defaults to `/api`). Keep it
relative (`/api`) so the Next.js rewrite proxies to the backend and the app
works from any device on the network — an absolute `http://localhost:8000/api`
is baked into the client bundle and breaks data loading on every device except
the dev machine (each browser would hit its own localhost).
