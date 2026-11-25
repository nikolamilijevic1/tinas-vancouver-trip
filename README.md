# Ottawa to Vancouver Flight Finder

Simple single-page web app to check live Ottawa (`YOW`) → Vancouver (`YVR`) round-trip prices across a flexible travel window, filter by nonstop or carry-on bag, jump straight to the matching Google Flights itinerary, and optionally get an email when a deal drops below a target price.

## Run Locally
```bash
./run.sh
```
Open `http://localhost:8000`.

## Free Permanent Hosting (Render — No Credit Card)
This repo includes a `Dockerfile` and `render.yaml` ready for Render's free Web Service tier:
1. Push this repo to GitHub.
2. Go to [dashboard.render.com](https://dashboard.render.com) → **New** → **Blueprint** → select the repo.
3. Render builds the Docker image and gives you a permanent `https://yow-yvr-flights.onrender.com` URL she can open anytime on her phone.

### Free Email Alerts (2 minutes)
Add either of these in Render → **Environment** (or locally before running `./run.sh`):
- **Resend (free tier: 3,000 emails/month):** set `RESEND_API_KEY` (and optionally `EMAIL_FROM`).
- **Gmail SMTP (free):** set `SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587`, `SMTP_USER=you@gmail.com`, and `SMTP_PASS=<16-char Gmail App Password>`.
