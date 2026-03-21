# PL Dashboard - Premier League Analyzer

A responsive web application for analyzing Premier League results, comparing teams, and predicting upcoming matchweek outcomes.

## Features

- **Matchweek Results** – Browse all 38 gameweeks with live scores and results
- **League Standings** – Full table with form indicators (last 5 games)
- **Team Comparison** – Head-to-head stats, charts, and recent form for any two teams
- **Predictions** – Simple predictions based on form, team strength, and home advantage

## Setup

```bash
pip install -r requirements.txt
python app.py
```

Open http://localhost:5000 in your browser (works on both desktop and mobile).

## Data Source

All data is fetched live from the official Fantasy Premier League API (`fantasy.premierleague.com`).

### Optional: Bookmaker odds (e.g. Bet365)

To show **market percentages** next to the AI predictions (no 365Scores/Bet365 website scraping – we use a proper API):

1. Get a free API key from [The Odds API](https://the-odds-api.com/) (free tier: 500 requests/month).
2. Copy `.env.example` to `.env` and add your key:
   ```
   ODDS_API_KEY=your_key_here
   ```
3. Run the app as usual. In the **Predictions** tab you’ll see two rows per match: **AI** and **Bet365** (or Market).

## Tech Stack

- **Backend:** Python / Flask
- **Frontend:** HTML5, CSS3, JavaScript (vanilla)
- **UI:** Bootstrap 5, Chart.js
- **Data:** Fantasy Premier League API
