# Options Report Dashboard

Professional dashboard for the current `Options Report`, built to mirror the existing markdown workflow in a cleaner browser view.

## What it shows

- `My Portfolio Report`
- `Covered Calls`
- `Cash Secured Puts`
- `Earnings This Week`
- `Team Review`
- report date tabs and a date filter

## Current deployment shape

This project is prepared for a **static Vercel deployment**, similar to the airline dashboard.

When deployed on Vercel today:

- `index.html` is the entrypoint
- the dashboard loads from the bundled snapshot files:
  - `latest_report.json`
  - `report_history.json`

That means the dashboard opens immediately as a polished static site with the latest prepared report data.

## Local preview

```powershell
& 'C:\Users\ncapi\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m http.server 8766 --bind 0.0.0.0
```

## Vercel import

1. Put this folder in a GitHub repository.
2. In Vercel, choose **Add New Project**.
3. Import that repository.
4. Keep the root as the project root.
5. Deploy with default settings.

## Files

- `index.html` - dashboard shell
- `styles.css` - dashboard styling
- `app.js` - dashboard rendering and date navigation
- `latest_report.json` - latest bundled snapshot
- `report_history.json` - historical snapshot list
