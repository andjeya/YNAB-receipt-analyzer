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

Set API base URL via `NEXT_PUBLIC_API_BASE_URL` (defaults to `http://localhost:8000/api`).
