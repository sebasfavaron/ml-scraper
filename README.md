# Mercado Libre Offers Scraper

A Python scraper that fetches daily deals from [Mercado Libre Argentina](https://www.mercadolibre.com.ar/ofertas) and generates a beautiful HTML report with price history verification.

## Features

- 🛒 **Scrapes multiple offer types** — Daily deals and flash offers (Ofertas Relámpago)
- 📊 **Price history verification** — Validates top discounts using [MercadoTrack](https://mercadotrack.com) to spot inflated "fake" discounts
- 📈 **Sparkline charts** — Visual price history for featured offers
- 🏷️ **Smart sorting** — Offers sorted by discount percentage (highest first)
- 🔄 **Deduplication** — Automatically removes duplicate listings
- 📝 **Rotating logs** — Debug logs with automatic rotation (5MB max, 3 backups)

## Quickstart

### 1. Clone and setup

```bash
git clone <your-repo-url>
cd ml-scraper
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Run the scraper

```bash
python scraper.py
```

### 3. View results

Open the generated `offers-YYYY-MM-DD.html` file in your browser.

## Output

The scraper generates an HTML file with:

- **Featured section** — Top 3 highest-discounted offers with price history analysis:
  - 🔥 Historical low price
  - ✅ Good price vs. average
  - ⚠️ Suspicious (price inflated before discount)
  - 📊 Normal price range
  
- **All offers grid** — Complete list of offers with images, prices, and discount badges

## Configuration

Edit these constants in `scraper.py` to customize:

| Variable | Default | Description |
|----------|---------|-------------|
| `pages_per_source` | 3 | Number of pages to scrape per offer type |
| `top_n` | 3 | Number of offers to verify with price history |

## Logs

Detailed logs are written to `scraper.log` with timestamps and severity levels. Console output shows a summary.

## Requirements

- Python 3.10+
- `requests` library

## License

MIT

