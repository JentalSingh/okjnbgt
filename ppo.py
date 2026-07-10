"""
Racial Equity LCI - Comment Form (Playwright + Proxy Rotation)
URL: https://racialequity.lci.ca.gov/comment-form/
PDF: 58fe4e7187ea3d743268e7d48e0edc6d.pdf
"""

from playwright.sync_api import sync_playwright
import json
import os
import random
import requests
import re
import time
from pathlib import Path

# ============================================================
# CONFIGURATION
# ============================================================
BASE_DIR = Path(__file__).resolve().parent

# 🔥 NEW TARGET URL
TARGET_URL = "https://racialequity.lci.ca.gov/comment-form/"

# 🔥 NEW PDF PATH
PDF_PATH = Path(r"C:\Users\JENTAL SINGH\Downloads\friday\58fe4e7187ea3d743268e7d48e0edc6d.pdf")
PDF_NAME = PDF_PATH.name
SITE_BASE = "https://racialequity.lci.ca.gov"

# Proxy file
PROXY_FILE = BASE_DIR / "proxies.txt"

# ============================================================
# PROXY FUNCTIONS
# ============================================================
def load_proxies():
    """Load proxies from file"""
    proxies = []
    if PROXY_FILE.exists():
        with open(PROXY_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    proxies.append(line)
        print(f"✅ Loaded {len(proxies)} proxies")
    else:
        print(f"⚠️ Proxy file not found: {PROXY_FILE}")
        print(f"   Using direct connection (no proxy)")
    return proxies

def parse_proxy(proxy_str):
    """Parse proxy string for Playwright"""
    proxy_str = proxy_str.replace('http://', '').replace('https://', '')
    parts = proxy_str.split(':')
    
    if len(parts) == 4:
        host, port, username, password = parts
        return {
            "server": f"http://{host}:{port}",
            "username": username,
            "password": password
        }
    elif len(parts) == 2:
        host, port = parts
        return {"server": f"http://{host}:{port}"}
    else:
        return {"server": f"http://{proxy_str}"}

def get_random_proxy():
    """Get random proxy from file"""
    proxies = load_proxies()
    if not proxies:
        return None
    
    proxy_str = random.choice(proxies)
    print(f"🌐 Selected proxy: {proxy_str}")
    return parse_proxy(proxy_str)

# ============================================================
# CHECK PDF
# ============================================================
def check_pdf():
    if PDF_PATH.exists():
        print(f"✅ PDF exists: {PDF_PATH}")
        print(f"📏 Size: {PDF_PATH.stat().st_size} bytes")
        return PDF_PATH
    else:
        print(f"❌ PDF not found: {PDF_PATH}")
        return None

# ============================================================
# EXTRACT PDF FILENAME FROM ANY DATA
# ============================================================
def extract_pdf_filename(value):
    if value is None:
        return None
    if isinstance(value, dict):
        preferred_keys = ("file", "file_name", "filename", "uploaded_file", "url")
        for key in preferred_keys:
            item = value.get(key)
            result = extract_pdf_filename(item)
            if result:
                return result
        for item in value.values():
            result = extract_pdf_filename(item)
            if result:
                return result
        return None
    if isinstance(value, list):
        for item in value:
            result = extract_pdf_filename(item)
            if result:
                return result
        return None
    match = re.search(r"([A-Za-z0-9._-]+\.pdf)", str(value), re.IGNORECASE)
    return match.group(1) if match else None

# ============================================================
# MAIN FUNCTION
# ============================================================
def main():
    print("\n" + "="*70)
    print("📄 RACIAL EQUITY LCI - COMMENT FORM")
    print("="*70)
    print(f"📁 Target: {TARGET_URL}")
    print(f"📄 PDF: {PDF_NAME}")
    print("="*70)
    
    # Check PDF
    pdf_path = check_pdf()
    if not pdf_path:
        return
    
    # Get random proxy
    proxy_config = get_random_proxy()
    
    with sync_playwright() as p:
        # Browser launch options
        launch_options = {
            "headless": False,
            "args": [
                "--start-maximized",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--ignore-certificate-errors",
                "--disable-blink-features=AutomationControlled",
                "--disable-gpu",
                "--disable-web-security",
                "--disable-features=IsolateOrigins,site-per-process",
            ]
        }
        
        browser = p.chromium.launch(**launch_options)
        
        # Context with proxy (if available)
        context_options = {
            "viewport": {"width": 1920, "height": 1080},
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        
        if proxy_config:
            context_options["proxy"] = proxy_config
            print(f"🌐 Using proxy: {proxy_config.get('server')}")
        else:
            print("🌐 Direct connection (no proxy)")
        
        context = browser.new_context(**context_options)
        page = context.new_page()
        
        pdf_url = None
        server_filename = None
        last_ajax_status = None
        last_ajax_text = ""
        
        # ============================================================
        # RESPONSE HANDLER
        # ============================================================
        def on_response(response):
            nonlocal pdf_url, server_filename, last_ajax_status, last_ajax_text
            
            try:
                url = response.url
                
                # Check for PDF in URL
                if '.pdf' in url:
                    pdf_url = url
                    print(f"\n📡 PDF URL found in request: {url}")
                    return
                
                # Check admin-ajax.php
                if "admin-ajax.php" in url:
                    text = response.text()
                    last_ajax_status = response.status
                    last_ajax_text = text[:2000]
                    
                    print(f"\n📡 admin-ajax response received")
                    
                    try:
                        data = json.loads(text)
                        file_name = extract_pdf_filename(data)
                        if file_name:
                            server_filename = file_name
                            print(f"   📄 Server filename: {server_filename}")
                    except:
                        file_name = extract_pdf_filename(text)
                        if file_name:
                            server_filename = file_name
                            print(f"   📄 Server filename: {server_filename}")
                    
            except Exception as e:
                pass
        
        def on_request(request):
            nonlocal pdf_url
            try:
                if '.pdf' in request.url:
                    pdf_url = request.url
                    print(f"\n📤 PDF Request: {request.url}")
            except:
                pass
        
        page.on("response", on_response)
        page.on("request", on_request)
        
        # ============================================================
        # NAVIGATE - WITH EXTRA WAIT FOR SLOW WEBSITE
        # ============================================================
        print(f"\n🌐 Opening: {TARGET_URL}")
        print("⏳ Website is slow - please wait...")
        
        # Try to load with longer timeout
        try:
            page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=60000)
        except:
            print("⚠️ Page load timeout, trying to continue...")
            page.goto(TARGET_URL, wait_until="commit", timeout=30000)
        
        # Extra wait for slow website
        print("⏳ Waiting for page to stabilize...")
        page.wait_for_timeout(5000)
        
        # Wait for body to be present
        try:
            page.wait_for_selector("body", timeout=30000)
            print("✅ Page loaded successfully")
        except:
            print("⚠️ Body not found, but continuing...")
        
        # ============================================================
        # FIND AND UPLOAD PDF
        # ============================================================
        print(f"\n📤 Uploading: {PDF_PATH.name}")
        
        try:
            # Wait for file input to be present
            try:
                page.wait_for_selector("input[type='file']", timeout=15000)
            except:
                print("⚠️ File input not found, trying to find any file input...")
            
            # Make file input visible
            page.evaluate("""
                var inputs = document.querySelectorAll('input[type="file"]');
                inputs.forEach(function(input) {
                    input.style.display = 'block';
                    input.style.visibility = 'visible';
                    input.style.opacity = '1';
                    input.style.height = 'auto';
                    input.style.width = 'auto';
                    input.style.position = 'relative';
                    input.style.zIndex = '9999';
                });
            """)
            page.wait_for_timeout(1000)
            
            # Upload file
            page.set_input_files("input[type='file']", str(pdf_path))
            print("✅ PDF uploaded!")
            
        except Exception as e:
            print(f"❌ Upload failed: {e}")
            
            # Try fallback: find by class
            try:
                page.set_input_files(".wpforms-file-upload input[type='file']", str(pdf_path))
                print("✅ PDF uploaded (fallback)!")
            except Exception as e2:
                print(f"❌ Fallback upload failed: {e2}")
                browser.close()
                return
        
        # ============================================================
        # WAIT FOR RESPONSE (45 seconds - extra for slow website)
        # ============================================================
        print("\n⏳ Waiting for upload response (website is slow)...")
        deadline = time.time() + 45
        
        while time.time() < deadline:
            if pdf_url or server_filename:
                break
            page.wait_for_timeout(1000)
            print(f"   ⏳ Still waiting... ({int(time.time() - (deadline - 45))}s elapsed)")
        
        page.wait_for_timeout(3000)
        
        # ============================================================
        # TRY CONSTRUCTED URL
        # ============================================================
        if server_filename and not pdf_url:
            constructed = f"{SITE_BASE}/wp-content/uploads/wpforms/tmp/{server_filename}"
            print(f"\n🔨 Trying constructed URL: {constructed}")
            try:
                r = requests.head(constructed, timeout=5)
                if r.status_code == 200:
                    pdf_url = constructed
                    print("   ✅ URL working!")
                else:
                    pdf_url = constructed
                    print("   ⚠️ URL may not exist, but using as fallback")
            except:
                pdf_url = constructed
                print("   ⚠️ Could not verify, using as fallback")
        
        # ============================================================
        # RESULTS
        # ============================================================
        print("\n" + "="*70)
        if pdf_url:
            pdf_url = pdf_url.replace('\\/', '/')
            print("✅ SUCCESS!")
            print(f"🔗 PDF URL: {pdf_url}")
            with open(BASE_DIR / "racial_equity_url.txt", "w") as f:
                f.write(pdf_url)
            print(f"📁 URL saved to: racial_equity_url.txt")
        else:
            print("⚠️ URL NOT CAPTURED")
            if server_filename:
                print(f"📄 Server filename: {server_filename}")
            if last_ajax_status is not None:
                print(f"📊 Last AJAX status: {last_ajax_status}")
            if last_ajax_text:
                print(f"📄 Last AJAX response preview: {last_ajax_text[:500]}")
            
            # Screenshot for debugging
            page.screenshot(path=str(BASE_DIR / "racial_equity_result.png"))
            print("📸 Screenshot saved: racial_equity_result.png")
        print("="*70)
        
        print("\n⏳ Browser open for 10 seconds...")
        page.wait_for_timeout(10000)
        browser.close()
        print("✅ Browser closed")

if __name__ == "__main__":
    main()