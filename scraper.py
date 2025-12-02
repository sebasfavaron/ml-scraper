#!/usr/bin/env python3
"""
Mercado Libre Daily Offers Scraper

Fetches offers from Mercado Libre Argentina and outputs them as HTML cards.
Includes price history verification via MercadoTrack for top discounted items.
"""

import json
import logging
import re
import requests
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Configure logging
LOG_FILE = Path(__file__).parent / "scraper.log"

def setup_logging():
    """Configure logging with both file and console output."""
    logger = logging.getLogger("ml-scraper")
    logger.setLevel(logging.DEBUG)
    
    # File handler with rotation (5MB max, keep 3 backups)
    file_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=5*1024*1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_format = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    file_handler.setFormatter(file_format)
    
    # Console handler (less verbose)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_format = logging.Formatter("%(message)s")
    console_handler.setFormatter(console_format)
    
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger

log = setup_logging()

BASE_URL = "https://www.mercadolibre.com.ar/ofertas"
MERCADOTRACK_URL = "https://mercadotrack.com/MLA/trackings"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# Different offer types to scrape
OFFER_SOURCES = [
    {"name": "Ofertas del Día", "params": {"container_id": "MLA779357-1"}},
    {"name": "Ofertas Relámpago", "params": {"container_id": "MLA779357-1", "promotion_type": "lightning"}},
]

# Regex to extract __PRELOADED_STATE__ JSON
PRELOADED_STATE_PATTERN = re.compile(
    r'window\.__PRELOADED_STATE__\s*=\s*(\{.+\});',
    re.DOTALL
)

# Regex to extract MLA product ID from URLs
MLA_ID_PATTERN = re.compile(r'MLA\d+')



def fetch_page(base_params: dict, page_num: int) -> str:
    """Fetch a single page of offers."""
    params = {**base_params, "page": page_num}
    response = requests.get(BASE_URL, params=params, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return response.text


def extract_mla_id(url: str) -> str | None:
    """Extract MLA product ID from a Mercado Libre URL."""
    match = MLA_ID_PATTERN.search(url)
    return match.group(0) if match else None


def extract_snapshots_json(text: str) -> list[dict] | None:
    """Extract snapshots array from MercadoTrack HTML."""
    # The JSON is escaped in the HTML, so look for escaped version
    marker = r'\"snapshots\":['
    start = text.find(marker)
    if start == -1:
        # Try unescaped version as fallback
        marker = '"snapshots":['
        start = text.find(marker)
        if start == -1:
            return None
    
    start += len(marker) - 1  # Position at the opening bracket
    
    # Find matching closing bracket
    depth = 0
    i = 0
    while i < len(text) - start:
        char = text[start + i]
        # Skip escaped characters
        if char == '\\' and i + 1 < len(text) - start:
            i += 2
            continue
        if char == '[':
            depth += 1
        elif char == ']':
            depth -= 1
            if depth == 0:
                end = start + i + 1
                json_str = text[start:end]
                # Unescape the string
                json_str = json_str.replace(r'\"', '"').replace(r'\\', '\\')
                try:
                    return json.loads(json_str)
                except json.JSONDecodeError:
                    return None
        i += 1
    return None


def fetch_price_history(mla_id: str) -> list[dict] | None:
    """Fetch price history from MercadoTrack for a product."""
    try:
        url = f"{MERCADOTRACK_URL}/{mla_id}"
        response = requests.get(url, headers=HEADERS, timeout=15)
        if response.status_code != 200:
            return None
        
        # Extract snapshots JSON from the HTML
        snapshots = extract_snapshots_json(response.text)
        if not snapshots:
            return None
        
        # Sort by date and return last 90 days of data
        snapshots.sort(key=lambda x: x.get("date", ""))
        return snapshots[-90:] if len(snapshots) > 90 else snapshots
    except Exception as e:
        print(f"    Error fetching price history for {mla_id}: {e}")
        return None


def analyze_price_history(snapshots: list[dict] | None, current_price: float) -> dict:
    """Analyze price history to determine if the offer is genuine."""
    if not snapshots:
        return {"status": "unknown", "message": "Sin historial"}
    
    prices = [s.get("price", 0) for s in snapshots if s.get("price")]
    if not prices:
        return {"status": "unknown", "message": "Sin datos de precio"}
    
    min_price = min(prices)
    max_price = max(prices)
    avg_price = sum(prices) / len(prices)
    
    # Get prices from last 30 days and before
    recent_prices = prices[-30:] if len(prices) > 30 else prices
    older_prices = prices[:-30] if len(prices) > 30 else []
    
    recent_avg = sum(recent_prices) / len(recent_prices) if recent_prices else avg_price
    older_avg = sum(older_prices) / len(older_prices) if older_prices else recent_avg
    
    # Determine if offer is genuine
    if current_price <= min_price * 1.05:  # Within 5% of all-time low
        status = "excellent"
        message = "🔥 Precio mínimo histórico"
    elif current_price < avg_price * 0.85:  # More than 15% below average
        status = "good"
        message = "✅ Buen precio vs. promedio"
    elif older_avg > 0 and recent_avg > older_avg * 1.1:  # Price inflated before discount
        status = "suspicious"
        message = "⚠️ Precio inflado antes del descuento"
    else:
        status = "normal"
        message = "📊 Precio dentro del rango normal"
    
    return {
        "status": status,
        "message": message,
        "min_price": min_price,
        "max_price": max_price,
        "avg_price": avg_price,
        "prices": prices
    }


def extract_preloaded_state(html: str) -> dict:
    """Extract the __PRELOADED_STATE__ JSON from HTML."""
    match = PRELOADED_STATE_PATTERN.search(html)
    if not match:
        raise ValueError("Could not find __PRELOADED_STATE__ in HTML")
    return json.loads(match.group(1))


def parse_items(state: dict) -> list[dict]:
    """Parse items from the preloaded state into a simple format."""
    items = state.get("data", {}).get("items", [])
    parsed = []
    
    for item in items:
        card = item.get("card", {})
        if not card:
            continue
        
        # Extract image URL from picture ID
        pictures = card.get("pictures", {}).get("pictures", [])
        image_url = None
        if pictures:
            pic_id = pictures[0].get("id")
            if pic_id:
                # ML image URL pattern
                image_url = f"https://http2.mlstatic.com/D_{pic_id}-O.jpg"
        
        # Extract URL from metadata
        metadata = card.get("metadata", {})
        url_path = metadata.get("url", "")
        link = f"https://{url_path}" if url_path else None
        
        # Extract title, discount, and price from components
        name = None
        discount = 0
        price = 0
        
        for comp in card.get("components", []):
            if comp.get("type") == "title":
                title_data = comp.get("title", {})
                name = title_data.get("text")
            
            elif comp.get("type") == "price":
                price_data = comp.get("price", {})
                discount_info = price_data.get("discount", {})
                if discount_info:
                    # Discount value is a number
                    discount = discount_info.get("value", 0)
                # Get current price
                current_price_data = price_data.get("current_price", {})
                if current_price_data:
                    price = current_price_data.get("value", 0)
        
        if name and link and image_url:
            parsed.append({
                "name": name,
                "link": link,
                "image": image_url,
                "discount": discount,
                "price": price
            })
    
    return parsed


def scrape_offers(pages_per_source: int = 3) -> list[dict]:
    """Scrape multiple pages from all offer sources, sorted by discount (highest first)."""
    all_offers = []
    seen_urls = set()  # Deduplicate by URL
    
    for source in OFFER_SOURCES:
        print(f"\n{source['name']}:")
        print("-" * 40)
        
        for page_num in range(1, pages_per_source + 1):
            print(f"  Fetching page {page_num}...")
            try:
                html = fetch_page(source["params"], page_num)
                state = extract_preloaded_state(html)
                offers = parse_items(state)
                
                # Deduplicate
                new_offers = []
                for offer in offers:
                    if offer["link"] not in seen_urls:
                        seen_urls.add(offer["link"])
                        new_offers.append(offer)
                
                all_offers.extend(new_offers)
                print(f"    Found {len(offers)} offers ({len(new_offers)} new)")
            except Exception as e:
                print(f"    Error on page {page_num}: {e}")
    
    # Sort by discount percentage (highest first)
    all_offers.sort(key=lambda x: x.get("discount", 0), reverse=True)
    
    return all_offers


def generate_sparkline_svg(prices: list[float], width: int = 200, height: int = 50) -> str:
    """Generate a simple SVG sparkline from price data."""
    if not prices or len(prices) < 2:
        return ""
    
    min_p = min(prices)
    max_p = max(prices)
    price_range = max_p - min_p if max_p != min_p else 1
    
    # Normalize points
    points = []
    for i, p in enumerate(prices):
        x = (i / (len(prices) - 1)) * width
        y = height - ((p - min_p) / price_range) * (height - 10) - 5
        points.append(f"{x:.1f},{y:.1f}")
    
    path = "M" + " L".join(points)
    
    return f'''<svg width="{width}" height="{height}" class="sparkline">
      <path d="{path}" fill="none" stroke="#3483fa" stroke-width="2"/>
      <circle cx="{width}" cy="{height - ((prices[-1] - min_p) / price_range) * (height - 10) - 5:.1f}" r="3" fill="#00a650"/>
    </svg>'''


def generate_featured_html(featured_offers: list[dict]) -> str:
    """Generate HTML for featured offers with price history."""
    if not featured_offers:
        return ""
    
    featured_cards = ""
    for offer in featured_offers:
        safe_name = (
            offer["name"]
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )
        discount = offer.get("discount", 0)
        price = offer.get("price", 0)
        price_formatted = f"${price:,.0f}".replace(",", ".")
        
        analysis = offer.get("price_analysis", {})
        status = analysis.get("status", "unknown")
        message = analysis.get("message", "Sin datos")
        prices = analysis.get("prices", [])
        min_price = analysis.get("min_price", 0)
        max_price = analysis.get("max_price", 0)
        avg_price = analysis.get("avg_price", 0)
        
        # Status colors
        status_colors = {
            "excellent": "#00a650",
            "good": "#3483fa",
            "suspicious": "#ff7733",
            "normal": "#666",
            "unknown": "#999"
        }
        status_color = status_colors.get(status, "#999")
        
        sparkline = generate_sparkline_svg(prices) if prices else '<span class="no-data">Sin historial</span>'
        
        mla_id = extract_mla_id(offer["link"]) or ""
        mercadotrack_link = f"https://mercadotrack.com/MLA/trackings/{mla_id}" if mla_id else "#"
        
        stats_html = ""
        if min_price > 0:
            stats_html = f'''
          <div class="price-stats">
            <span>Mín: ${min_price:,.0f}</span>
            <span>Prom: ${avg_price:,.0f}</span>
            <span>Máx: ${max_price:,.0f}</span>
          </div>'''
        
        featured_cards += f'''
    <div class="featured-card">
      <div class="featured-image">
        <span class="discount">{discount}% OFF</span>
        <img src="{offer["image"]}" alt="{safe_name}">
      </div>
      <div class="featured-info">
        <a href="{offer["link"]}" target="_blank" class="featured-title">{safe_name}</a>
        <div class="featured-price">{price_formatted}</div>
        <div class="price-history">
          <div class="analysis-badge" style="background: {status_color}">{message}</div>
          {sparkline}
          {stats_html}
          <a href="{mercadotrack_link}" target="_blank" class="mercadotrack-link">Ver historial completo →</a>
        </div>
      </div>
    </div>'''
    
    return f'''
  <section class="featured-section">
    <h2>🔍 Top 3 Ofertas - Análisis de Precio</h2>
    <p class="featured-subtitle">Verificamos el historial de precios para confirmar si son ofertas reales</p>
    <div class="featured-grid">{featured_cards}
    </div>
  </section>'''


def generate_html(offers: list[dict], featured_offers: list[dict] = None) -> str:
    """Generate HTML output with offer cards."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    featured_html = generate_featured_html(featured_offers) if featured_offers else ""
    
    cards_html = ""
    for offer in offers:
        # Escape HTML entities in name
        safe_name = (
            offer["name"]
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )
        discount = offer.get("discount", 0)
        price = offer.get("price", 0)
        discount_badge = f'<span class="discount">{discount}% OFF</span>' if discount > 0 else ""
        price_formatted = f"${price:,.0f}".replace(",", ".")
        
        cards_html += f'''
    <div class="card">
      {discount_badge}
      <img src="{offer["image"]}" alt="{safe_name}" loading="lazy">
      <span class="price">{price_formatted}</span>
      <a href="{offer["link"]}" target="_blank">{safe_name}</a>
    </div>'''
    
    return f'''<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Mercado Libre - Ofertas del Día</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: #f5f5f5;
      padding: 20px;
    }}
    h1 {{
      text-align: center;
      color: #333;
      margin-bottom: 10px;
    }}
    h2 {{
      color: #333;
      margin-bottom: 8px;
    }}
    .meta {{
      text-align: center;
      color: #666;
      margin-bottom: 20px;
      font-size: 14px;
    }}
    
    /* Featured Section */
    .featured-section {{
      max-width: 1400px;
      margin: 0 auto 40px;
      background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
      border-radius: 16px;
      padding: 24px;
      color: white;
    }}
    .featured-section h2 {{
      color: white;
      font-size: 22px;
    }}
    .featured-subtitle {{
      color: #aaa;
      font-size: 14px;
      margin-bottom: 20px;
    }}
    .featured-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(380px, 1fr));
      gap: 20px;
    }}
    .featured-card {{
      background: rgba(255,255,255,0.08);
      border-radius: 12px;
      padding: 16px;
      display: grid;
      grid-template-columns: 140px 1fr;
      gap: 16px;
    }}
    .featured-image {{
      position: relative;
      background: white;
      border-radius: 8px;
      padding: 8px;
    }}
    .featured-image img {{
      width: 100%;
      height: 120px;
      object-fit: contain;
    }}
    .featured-image .discount {{
      position: absolute;
      top: -8px;
      right: -8px;
      background: #00a650;
      color: white;
      font-size: 12px;
      font-weight: bold;
      padding: 4px 8px;
      border-radius: 6px;
    }}
    .featured-info {{
      display: flex;
      flex-direction: column;
      gap: 8px;
    }}
    .featured-title {{
      color: white;
      text-decoration: none;
      font-size: 14px;
      line-height: 1.4;
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
      overflow: hidden;
    }}
    .featured-title:hover {{
      text-decoration: underline;
    }}
    .featured-price {{
      font-size: 24px;
      font-weight: bold;
      color: #00a650;
    }}
    .price-history {{
      display: flex;
      flex-direction: column;
      gap: 6px;
    }}
    .analysis-badge {{
      display: inline-block;
      padding: 4px 10px;
      border-radius: 12px;
      font-size: 12px;
      font-weight: 500;
      color: white;
      width: fit-content;
    }}
    .sparkline {{
      margin: 4px 0;
    }}
    .price-stats {{
      display: flex;
      gap: 12px;
      font-size: 11px;
      color: #999;
    }}
    .price-stats span {{
      display: inline-block;
    }}
    .mercadotrack-link {{
      color: #3483fa;
      text-decoration: none;
      font-size: 12px;
    }}
    .mercadotrack-link:hover {{
      text-decoration: underline;
    }}
    .no-data {{
      color: #666;
      font-size: 12px;
      font-style: italic;
    }}
    
    /* Regular Grid */
    .all-offers-title {{
      max-width: 1400px;
      margin: 0 auto 16px;
      color: #333;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
      gap: 16px;
      max-width: 1400px;
      margin: 0 auto;
    }}
    .card {{
      background: white;
      border-radius: 8px;
      padding: 12px;
      box-shadow: 0 1px 3px rgba(0,0,0,0.1);
      display: flex;
      flex-direction: column;
      align-items: center;
      position: relative;
    }}
    .card img {{
      width: 100%;
      height: 150px;
      object-fit: contain;
      margin-bottom: 10px;
    }}
    .card a {{
      color: #3483fa;
      text-decoration: none;
      font-size: 13px;
      text-align: center;
      line-height: 1.3;
    }}
    .card a:hover {{
      text-decoration: underline;
    }}
    .discount {{
      position: absolute;
      top: 8px;
      right: 8px;
      background: #00a650;
      color: white;
      font-size: 11px;
      font-weight: bold;
      padding: 3px 6px;
      border-radius: 4px;
    }}
    .price {{
      font-size: 18px;
      font-weight: bold;
      color: #333;
      margin-bottom: 8px;
    }}
    
    @media (max-width: 480px) {{
      .featured-grid {{
        grid-template-columns: 1fr;
      }}
      .featured-card {{
        grid-template-columns: 1fr;
      }}
    }}
  </style>
</head>
<body>
  <h1>Ofertas del Día - Mercado Libre</h1>
  <p class="meta">Actualizado: {timestamp} | {len(offers)} ofertas (ordenadas por descuento)</p>
  {featured_html}
  <h3 class="all-offers-title">Todas las ofertas</h3>
  <div class="grid">{cards_html}
  </div>
</body>
</html>
'''


def fetch_top_offers_history(offers: list[dict], top_n: int = 3) -> list[dict]:
    """Fetch price history for the top N discounted offers."""
    print(f"\n🔍 Verificando historial de precios para top {top_n} ofertas...")
    print("-" * 40)
    
    featured = []
    for i, offer in enumerate(offers[:top_n]):
        mla_id = extract_mla_id(offer["link"])
        print(f"  [{i+1}/{top_n}] {offer['name'][:50]}...")
        
        if mla_id:
            print(f"    → Buscando {mla_id} en MercadoTrack...")
            snapshots = fetch_price_history(mla_id)
            analysis = analyze_price_history(snapshots, offer.get("price", 0))
            offer_copy = offer.copy()
            offer_copy["price_analysis"] = analysis
            offer_copy["mla_id"] = mla_id
            featured.append(offer_copy)
            print(f"    → {analysis['message']}")
        else:
            print(f"    → No se pudo extraer MLA ID")
            offer_copy = offer.copy()
            offer_copy["price_analysis"] = {"status": "unknown", "message": "ID no encontrado"}
            featured.append(offer_copy)
    
    return featured


def main():
    print("Mercado Libre Offers Scraper")
    print("=" * 40)
    
    offers = scrape_offers(pages_per_source=3)
    print(f"\nTotal offers collected: {len(offers)}")
    
    # Fetch price history for top 3 discounted offers
    featured_offers = fetch_top_offers_history(offers, top_n=3)
    
    html = generate_html(offers, featured_offers)
    
    output_file = "offers.html"
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(html)
    
    print(f"\nOutput written to: {output_file}")


if __name__ == "__main__":
    main()

