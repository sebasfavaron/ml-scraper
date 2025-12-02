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
MERCADOTRACK_FEATURED_URL = "https://mercadotrack.com/MLA"
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

# Regex to extract image ID from mlstatic URLs
MLSTATIC_IMAGE_PATTERN = re.compile(r'https?://http2\.mlstatic\.com/D_([^-]+)-')



def fetch_mercadotrack_featured() -> list[dict]:
    """Fetch featured offers from MercadoTrack Argentina."""
    log.info("\n📊 Fetching MercadoTrack Featured Offers...")
    log.info("-" * 40)
    
    try:
        start_time = datetime.now()
        response = requests.get(MERCADOTRACK_FEATURED_URL, headers=HEADERS, timeout=30)
        elapsed = (datetime.now() - start_time).total_seconds()
        
        log.debug(f"MercadoTrack response: status={response.status_code}, time={elapsed:.2f}s")
        
        if response.status_code != 200:
            log.error(f"MercadoTrack returned {response.status_code}")
            return []
        
        html = response.text
        offers = []
        
        # Parse offer cards from the HTML
        # Look for links to /MLA/trackings/MLA{id} in the "Ofertas destacadas" section
        # The section is between "Ofertas destacadas" and "Ultimos trackeados"
        featured_start = html.find("Ofertas destacadas")
        featured_end = html.find("Ultimos trackeados")
        
        if featured_start == -1:
            log.warning("Could not find 'Ofertas destacadas' section")
            return []
        
        featured_section = html[featured_start:featured_end] if featured_end > featured_start else html[featured_start:]
        
        # Extract all MLA IDs from tracking links
        tracking_pattern = re.compile(r'/MLA/trackings/(MLA\d+)')
        mla_ids = tracking_pattern.findall(featured_section)
        
        # Remove duplicates while preserving order
        seen = set()
        unique_mla_ids = []
        for mla_id in mla_ids:
            if mla_id not in seen:
                seen.add(mla_id)
                unique_mla_ids.append(mla_id)
        
        log.info(f"  Found {len(unique_mla_ids)} featured offers")
        
        # Extract product data for each MLA ID
        for mla_id in unique_mla_ids:
            # Find the block containing this MLA ID
            idx = featured_section.find(f'/MLA/trackings/{mla_id}')
            if idx == -1:
                continue
            
            # Get a chunk of HTML around this link to extract data
            block_start = max(0, idx - 100)
            block_end = min(len(featured_section), idx + 2000)
            block = featured_section[block_start:block_end]
            
            # Extract product name - look for the title after the image
            name = None
            # Try to find text content after "Featured image" alt text
            name_patterns = [
                re.compile(r'<p[^>]*>([^<]{10,200})</p>'),  # First substantial <p> tag
            ]
            
            for pattern in name_patterns:
                matches = pattern.findall(block)
                for match in matches:
                    # Skip prices (they contain $)
                    if '$' not in match and 'Hace' not in match and len(match.strip()) > 10:
                        name = match.strip()
                        break
                if name:
                    break
            
            # Extract prices using pattern like "$ 340.564,03"
            price_pattern = re.compile(r'\$\s*([\d.,]+)')
            prices = price_pattern.findall(block)
            
            original_price = 0
            current_price = 0
            if len(prices) >= 2:
                # First price is original, second is current (discounted)
                try:
                    original_price = float(prices[0].replace('.', '').replace(',', '.'))
                    current_price = float(prices[1].replace('.', '').replace(',', '.'))
                except ValueError:
                    pass
            elif len(prices) == 1:
                try:
                    current_price = float(prices[0].replace('.', '').replace(',', '.'))
                except ValueError:
                    pass
            
            # Extract discount percentage - look for patterns like "-11,85%" or "-11.85%"
            # Avoid matching percentages in product names (like "87% Tkl")
            discount = 0
            # Look for discount badge pattern: negative percentage with optional quotes
            discount_pattern = re.compile(r'[>"\'](-[\d.,]+%)["\']?<')
            discount_matches = discount_pattern.findall(block)
            if discount_matches:
                try:
                    # Remove the leading dash and trailing %
                    discount_str = discount_matches[0].strip('-').strip('%')
                    discount = float(discount_str.replace(',', '.'))
                except ValueError:
                    pass
            
            # Calculate discount if we have both prices and no explicit discount
            if discount == 0 and original_price > 0 and current_price > 0 and original_price > current_price:
                discount = round((1 - current_price / original_price) * 100, 2)
            
            # Extract image URL
            image_url = None
            image_pattern = re.compile(r'https?://http2\.mlstatic\.com/D_[^"\'>\s]+')
            image_matches = image_pattern.findall(block)
            if image_matches:
                image_url = image_matches[0]
            
            # Build the offer object
            if name or mla_id:
                offer = {
                    "name": name or f"Producto {mla_id}",
                    "link": f"https://mercadolibre.com.ar/p/{mla_id}",
                    "mercadotrack_link": f"https://mercadotrack.com/MLA/trackings/{mla_id}",
                    "image": image_url or f"https://http2.mlstatic.com/D_{mla_id}-O.jpg",
                    "price": current_price,
                    "original_price": original_price,
                    "discount": discount,
                    "mla_id": mla_id
                }
                offers.append(offer)
                log.debug(f"  → {name[:50] if name else mla_id}... ({discount:.1f}% OFF)")
        
        log.info(f"  Successfully parsed {len(offers)} offers")
        return offers
        
    except requests.exceptions.Timeout:
        log.error("Timeout fetching MercadoTrack featured offers")
        return []
    except requests.exceptions.RequestException as e:
        log.error(f"Network error fetching MercadoTrack: {e}")
        return []
    except Exception as e:
        log.error(f"Unexpected error fetching MercadoTrack: {type(e).__name__}: {e}")
        return []


def fetch_page(base_params: dict, page_num: int) -> str:
    """Fetch a single page of offers."""
    params = {**base_params, "page": page_num}
    log.debug(f"Fetching page {page_num} with params: {params}")
    
    start_time = datetime.now()
    response = requests.get(BASE_URL, params=params, headers=HEADERS, timeout=30)
    elapsed = (datetime.now() - start_time).total_seconds()
    
    log.debug(f"Response: status={response.status_code}, size={len(response.text)} bytes, time={elapsed:.2f}s")
    
    # Log potential rate limiting indicators
    if response.status_code == 429:
        log.warning(f"RATE LIMITED (429) on page {page_num} - consider adding delays")
    elif response.status_code == 503:
        log.warning(f"SERVICE UNAVAILABLE (503) on page {page_num} - possible rate limiting")
    elif elapsed > 5:
        log.warning(f"Slow response ({elapsed:.2f}s) - possible throttling")
    
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
    url = f"{MERCADOTRACK_URL}/{mla_id}"
    log.debug(f"Fetching price history for {mla_id} from MercadoTrack")
    
    try:
        start_time = datetime.now()
        response = requests.get(url, headers=HEADERS, timeout=15)
        elapsed = (datetime.now() - start_time).total_seconds()
        
        log.debug(f"MercadoTrack response: status={response.status_code}, time={elapsed:.2f}s")
        
        if response.status_code == 429:
            log.warning(f"MercadoTrack RATE LIMITED (429) for {mla_id}")
            return None
        elif response.status_code != 200:
            log.debug(f"MercadoTrack returned {response.status_code} for {mla_id}")
            return None
        
        # Extract snapshots JSON from the HTML
        snapshots = extract_snapshots_json(response.text)
        if not snapshots:
            log.debug(f"No price snapshots found for {mla_id}")
            return None
        
        log.info(f"Found {len(snapshots)} price snapshots for {mla_id}")
        
        # Sort by date and return last 90 days of data
        snapshots.sort(key=lambda x: x.get("date", ""))
        return snapshots[-90:] if len(snapshots) > 90 else snapshots
    except requests.exceptions.Timeout:
        log.error(f"Timeout fetching price history for {mla_id}")
        return None
    except requests.exceptions.RequestException as e:
        log.error(f"Network error fetching price history for {mla_id}: {e}")
        return None
    except Exception as e:
        log.error(f"Unexpected error fetching price history for {mla_id}: {e}")
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
        log.info(f"\n{source['name']}:")
        log.info("-" * 40)
        
        for page_num in range(1, pages_per_source + 1):
            log.info(f"  Fetching page {page_num}...")
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
                log.info(f"    Found {len(offers)} offers ({len(new_offers)} new)")
            except requests.exceptions.HTTPError as e:
                log.error(f"HTTP error on {source['name']} page {page_num}: {e}")
            except requests.exceptions.Timeout:
                log.error(f"Timeout on {source['name']} page {page_num}")
            except requests.exceptions.RequestException as e:
                log.error(f"Network error on {source['name']} page {page_num}: {e}")
            except ValueError as e:
                log.error(f"Parse error on {source['name']} page {page_num}: {e}")
            except Exception as e:
                log.error(f"Unexpected error on {source['name']} page {page_num}: {type(e).__name__}: {e}")
    
    # Sort by discount percentage (highest first)
    all_offers.sort(key=lambda x: x.get("discount", 0), reverse=True)
    
    log.debug(f"Total unique offers after deduplication: {len(all_offers)}")
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


def generate_mercadotrack_featured_html(mt_offers: list[dict]) -> str:
    """Generate HTML for MercadoTrack featured offers section."""
    if not mt_offers:
        return ""
    
    cards_html = ""
    for offer in mt_offers:
        safe_name = (
            offer["name"]
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )
        discount = offer.get("discount", 0)
        price = offer.get("price", 0)
        original_price = offer.get("original_price", 0)
        price_formatted = f"${price:,.0f}".replace(",", ".")
        original_formatted = f"${original_price:,.0f}".replace(",", ".") if original_price > 0 else ""
        
        discount_badge = f'<span class="mt-discount">-{discount:.1f}%</span>' if discount > 0 else ""
        original_html = f'<span class="mt-original">{original_formatted}</span>' if original_price > 0 else ""
        
        image_url = offer.get("image", "")
        mercadotrack_link = offer.get("mercadotrack_link", "#")
        
        cards_html += f'''
      <a href="{mercadotrack_link}" target="_blank" class="mt-card">
        <div class="mt-image">
          {discount_badge}
          <img src="{image_url}" alt="{safe_name}" loading="lazy">
        </div>
        <div class="mt-info">
          <span class="mt-name">{safe_name}</span>
          <div class="mt-prices">
            {original_html}
            <span class="mt-price">{price_formatted}</span>
          </div>
        </div>
      </a>'''
    
    return f'''
  <section class="mercadotrack-section">
    <div class="mt-header">
      <h2>🏷️ Ofertas Destacadas - MercadoTrack</h2>
      <a href="https://mercadotrack.com/MLA" target="_blank" class="mt-view-all">Ver todas en MercadoTrack →</a>
    </div>
    <p class="mt-subtitle">Ofertas con historial de precios verificado por la comunidad de MercadoTrack</p>
    <div class="mt-grid">{cards_html}
    </div>
  </section>'''


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
    <p class="featured-subtitle">Historial de precios verificado con <a href="https://mercadotrack.com" target="_blank" style="color: #3483fa;">MercadoTrack</a> para confirmar si son ofertas reales</p>
    <div class="featured-grid">{featured_cards}
    </div>
  </section>'''


def generate_html(offers: list[dict], featured_offers: list[dict] | None = None, mt_offers: list[dict] | None = None) -> str:
    """Generate HTML output with offer cards."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    mt_html = generate_mercadotrack_featured_html(mt_offers) if mt_offers else ""
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
    .top-nav {{
      max-width: 1400px;
      margin: 0 auto 20px;
      display: flex;
      gap: 16px;
      justify-content: center;
    }}
    .top-nav a {{
      color: #3483fa;
      text-decoration: none;
      padding: 8px 16px;
      background: white;
      border-radius: 20px;
      font-size: 14px;
      box-shadow: 0 1px 3px rgba(0,0,0,0.1);
      transition: all 0.2s;
    }}
    .top-nav a:hover {{
      background: #3483fa;
      color: white;
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
    
    /* MercadoTrack Featured Section */
    .mercadotrack-section {{
      max-width: 1400px;
      margin: 0 auto 32px;
      background: linear-gradient(135deg, #ff6b35 0%, #f7931e 50%, #ffd23f 100%);
      border-radius: 16px;
      padding: 24px;
      color: white;
    }}
    .mt-header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 12px;
    }}
    .mercadotrack-section h2 {{
      color: white;
      font-size: 22px;
      margin: 0;
    }}
    .mt-view-all {{
      color: white;
      text-decoration: none;
      font-size: 14px;
      background: rgba(0,0,0,0.2);
      padding: 8px 16px;
      border-radius: 20px;
      transition: background 0.2s;
    }}
    .mt-view-all:hover {{
      background: rgba(0,0,0,0.35);
    }}
    .mt-subtitle {{
      color: rgba(255,255,255,0.85);
      font-size: 14px;
      margin: 8px 0 20px;
    }}
    .mt-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
      gap: 16px;
    }}
    .mt-card {{
      background: rgba(255,255,255,0.95);
      border-radius: 12px;
      padding: 12px;
      display: flex;
      gap: 12px;
      text-decoration: none;
      color: #333;
      transition: transform 0.2s, box-shadow 0.2s;
    }}
    .mt-card:hover {{
      transform: translateY(-2px);
      box-shadow: 0 8px 24px rgba(0,0,0,0.15);
    }}
    .mt-image {{
      position: relative;
      flex-shrink: 0;
      width: 80px;
      height: 80px;
      background: white;
      border-radius: 8px;
      display: flex;
      align-items: center;
      justify-content: center;
    }}
    .mt-image img {{
      max-width: 100%;
      max-height: 100%;
      object-fit: contain;
    }}
    .mt-discount {{
      position: absolute;
      top: -6px;
      right: -6px;
      background: #e53935;
      color: white;
      font-size: 11px;
      font-weight: bold;
      padding: 3px 6px;
      border-radius: 4px;
    }}
    .mt-info {{
      display: flex;
      flex-direction: column;
      justify-content: center;
      gap: 6px;
      min-width: 0;
    }}
    .mt-name {{
      font-size: 13px;
      line-height: 1.3;
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
      overflow: hidden;
      color: #333;
    }}
    .mt-prices {{
      display: flex;
      align-items: center;
      gap: 8px;
    }}
    .mt-original {{
      font-size: 12px;
      color: #999;
      text-decoration: line-through;
    }}
    .mt-price {{
      font-size: 16px;
      font-weight: bold;
      color: #00a650;
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
      margin: 0 auto 8px;
      color: #333;
    }}
    .all-offers-subtitle {{
      max-width: 1400px;
      margin: 0 auto 16px;
      color: #666;
      font-size: 14px;
    }}
    .all-offers-subtitle a {{
      color: #3483fa;
      text-decoration: none;
    }}
    .all-offers-subtitle a:hover {{
      text-decoration: underline;
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
  <nav class="top-nav">
    <a href="index.html" class="nav-home">🏠 Inicio</a>
    <a href="archive.html" class="nav-archive">📅 Ver archivo</a>
  </nav>
  <h1>Ofertas del Día - Mercado Libre</h1>
  <p class="meta">Actualizado: {timestamp} | {len(offers)} ofertas (ordenadas por descuento)</p>
  {mt_html}
  {featured_html}
  <h3 class="all-offers-title">Todas las ofertas</h3>
  <p class="all-offers-subtitle">Ofertas del Día y Ofertas Relámpago extraídas de <a href="https://www.mercadolibre.com.ar/ofertas" target="_blank">mercadolibre.com.ar/ofertas</a>, ordenadas por descuento</p>
  <div class="grid">{cards_html}
  </div>
</body>
</html>
'''


def update_offers_manifest(offers_dir: Path) -> None:
    """Update manifest.json with list of all offer files, sorted by date (newest first)."""
    offer_files = sorted(
        [f.name for f in offers_dir.glob("offers-*.html")],
        reverse=True  # Newest first
    )
    
    manifest = {
        "updated": datetime.now().isoformat(),
        "latest": offer_files[0] if offer_files else None,
        "files": offer_files
    }
    
    manifest_path = offers_dir / "manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    
    log.info(f"Updated manifest.json with {len(offer_files)} files")


def fetch_top_offers_history(offers: list[dict], top_n: int = 3) -> list[dict]:
    """Fetch price history for the top N discounted offers."""
    log.info(f"\n🔍 Verificando historial de precios para top {top_n} ofertas...")
    log.info("-" * 40)
    
    featured = []
    for i, offer in enumerate(offers[:top_n]):
        mla_id = extract_mla_id(offer["link"])
        log.info(f"  [{i+1}/{top_n}] {offer['name'][:50]}...")
        
        if mla_id:
            log.info(f"    → Buscando {mla_id} en MercadoTrack...")
            snapshots = fetch_price_history(mla_id)
            analysis = analyze_price_history(snapshots, offer.get("price", 0))
            offer_copy = offer.copy()
            offer_copy["price_analysis"] = analysis
            offer_copy["mla_id"] = mla_id
            featured.append(offer_copy)
            log.info(f"    → {analysis['message']}")
        else:
            log.warning(f"    → No se pudo extraer MLA ID from {offer['link']}")
            offer_copy = offer.copy()
            offer_copy["price_analysis"] = {"status": "unknown", "message": "ID no encontrado"}
            featured.append(offer_copy)
    
    return featured


def main():
    start_time = datetime.now()
    log.info("=" * 50)
    log.info("Mercado Libre Offers Scraper - Run Started")
    log.info(f"Timestamp: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    log.info("=" * 50)
    
    try:
        # Fetch MercadoTrack featured offers first
        mt_offers = fetch_mercadotrack_featured()
        
        offers = scrape_offers(pages_per_source=3)
        log.info(f"\nTotal offers collected: {len(offers)}")
        
        # Fetch price history for top 3 discounted offers
        featured_offers = fetch_top_offers_history(offers, top_n=3)
        
        html = generate_html(offers, featured_offers, mt_offers)
        
        # Ensure docs directory exists (GitHub Pages standard folder)
        offers_dir = Path(__file__).parent / "docs"
        offers_dir.mkdir(exist_ok=True)
        
        output_file = offers_dir / f"offers-{start_time.strftime('%Y-%m-%d')}.html"
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(html)
        
        # Update manifest.json with list of all offer files
        update_offers_manifest(offers_dir)
        
        elapsed = (datetime.now() - start_time).total_seconds()
        log.info(f"\nOutput written to: {output_file}")
        log.info(f"Run completed successfully in {elapsed:.1f}s")
        log.info(f"Summary: {len(offers)} offers, {len(featured_offers)} with price history, {len(mt_offers)} MercadoTrack featured")
        
    except Exception as e:
        log.error(f"Fatal error during scrape: {type(e).__name__}: {e}")
        raise
    finally:
        log.info("=" * 50)
        log.info("Scraper run finished")
        log.info("=" * 50 + "\n")


if __name__ == "__main__":
    main()

