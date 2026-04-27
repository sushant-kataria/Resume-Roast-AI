# ResumeRoast AI 🔥

A Telegram bot that roasts resumes using Google Gemini AI and monetizes via Telegram Stars.

---

## What it does

Users paste their resume (text or PDF) and the bot delivers a brutally honest, AI-powered critique:

- **Free tier** — 3-point roast, once per day
- **50 Stars** — Full 10-point roast with exact quotes and concrete fixes
- **150 Stars** — Full roast + AI rewrite of Summary, Skills, and top Experience bullets

Includes a referral system: share your link, earn extra free roasts.

---

## Pricing Tiers

| Tier | Cost | What you get |
|------|------|-------------|
| Free | 0 | 3-point roast, once per day |
| Full Roast | 50 Stars (~$0.50) | 10-point breakdown, quotes + fixes |
| Roast + Rewrite | 150 Stars (~$1.50) | Full roast + before/after rewrites |

---

## Setup in 4 Steps

### 1. Create your bot in BotFather

1. Open Telegram → search **@BotFather**
2. Send `/newbot` and follow prompts
3. Copy the **BOT_TOKEN**
4. Send `/mybots` → your bot → *Payments* → connect a payment provider (use **Stars** — no external provider needed)

### 2. Enable Telegram Stars payments

In BotFather: `/mybots` → select your bot → **Bot Settings** → **Payments** → choose **Telegram Stars**.

### 3. Get a Gemini API key

1. Go to [aistudio.google.com](https://aistudio.google.com)
2. Click **Get API key** → **Create API key**
3. Copy the key (free tier: 1,500 requests/day)

### 4. Deploy to Railway

1. Fork this repo to your GitHub account
2. Go to [railway.app](https://railway.app) → **New Project** → **Deploy from GitHub repo**
3. Select your fork
4. Add environment variables (see below)
5. Railway auto-detects `railway.toml` and starts the bot

---

## Environment Variables

| Variable | Description |
|----------|-------------|
| `BOT_TOKEN` | Telegram bot token from BotFather |
| `GEMINI_API_KEY` | Google Gemini API key from AI Studio |
| `BOT_USERNAME` | Your bot's username (without @), e.g. `ResumeRoastBot` |

Copy `.env.example` to `.env` and fill in the values for local testing.

---

## Local Testing

```bash
# 1. Clone the repo
git clone https://github.com/youruser/resume-roast-ai.git
cd resume-roast-ai

# 2. Create virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Set up environment
cp .env.example .env
# Edit .env and add your BOT_TOKEN and GEMINI_API_KEY

# 5. Run
python bot.py
```

The bot runs in long-polling mode — no webhook setup needed for local testing.

---

## Revenue Projections

Assumes 5% of daily active users convert to paid, 50/50 split between tiers.

| Daily Active Users | Paid Users/Day | Avg Revenue/Day | Monthly Revenue |
|--------------------|---------------|-----------------|-----------------|
| 100 | 5 | ~$5.00 | ~$150 |
| 500 | 25 | ~$25.00 | ~$750 |
| 1,000 | 50 | ~$50.00 | ~$1,500 |

> Telegram Stars: Telegram keeps ~30% for in-app purchases on iOS/Android. Payouts via Fragment.

---

## Scale-Up Roadmap

| Phase | What to add |
|-------|-------------|
| **v1** | Current: polling bot, in-memory state |
| **v2** | Swap dicts for PostgreSQL (Railway addon), add Redis for rate-limiting |
| **v3** | Webhook mode instead of polling (lower latency) |
| **v4** | Multiple language support, LinkedIn URL input |
| **v5** | Weekly digest emails, resume score history dashboard |
| **v6** | Affiliate dashboard, bulk B2B pricing for bootcamps |

---

## Project Structure

```
resumeroast_bot/
├── bot.py           # Main bot — handlers, payment flow, Gemini calls
├── prompts.py       # All AI prompt templates
├── requirements.txt
├── railway.toml     # Railway hosting config
├── .env.example     # Environment variable template
├── .gitignore
└── README.md
```

---

## License

MIT
