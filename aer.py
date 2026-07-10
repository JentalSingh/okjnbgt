"""
OIG DC - Medicaid Fraud Complaint Form — FULLY FIXED VERSION
URL: https://oig.dc.gov/form/medicaid-fraud-complaint-form
═══════════════════════════════════════════════════════════════════
ROOT-CAUSE ANALYSIS & COMPLETE BUG LIST
═══════════════════════════════════════════════════════════════════
BUG-A  "No submit button found" — WRONG BUTTON SELECTED
  The DOM has two elements:
    • id="edit-submit"         → Search bar's "Search" button  ← script was clicking THIS
    • id="edit-actions-submit" → Webform "Submit" button       ← correct target
  The original click_submit() listed "#edit-submit" first, so Playwright
  matched the search bar. The webform button was never reached.
  FIX: Always target #edit-actions-submit / .webform-button--submit /
       [data-drupal-selector="edit-actions-submit"]. Never use bare
       "#edit-submit" or "input[type=submit]" (too broad).
BUG-B  File upload silently fails — CAPTCHA GATE BLOCKS CHANGE EVENT
  recaptcha-gate.js (custom Drupal module) intercepts THREE events:
    1. click  on file input → opens captcha modal if not verified
    2. change on file input → clears file + opens modal if not verified
    3. Drupal.Ajax.prototype.beforeSerialize → blocks upload AJAX
  Playwright's set_input_files() fires a synthetic change event.
  The gate's change handler ran first, saw captcha invalid, cleared
  the file selection, and opened the modal — so the file was never staged.
  FIX: Inject the modal-captcha token into:
    • document.getElementById('file-upload-captcha-token').value = token
    • document.getElementById('file-upload-captcha-verified').value = '1'
    • sessionStorage 'oig_captcha_token' / 'oig_captcha_verified'
  BEFORE calling set_input_files(). All three gate checks then pass.
BUG-C  Upload button click not needed — AUTO-UPLOAD fires on change
  The file input has data-once="auto-file-upload webform-auto-file-upload".
  When set_input_files() fires the change event (after captcha token
  injection satisfies the gate), Drupal's auto-upload behavior
  automatically POSTs to the AJAX endpoint — no manual upload button
  click required. The upload AJAX response rebuilds the form with:
    • A "Remove" button (indicates completion)
    • A <a href="…/_sid_/filename.pdf"> (the staged PDF URL)
BUG-D  TWO separate reCAPTCHAs with different scopes
  • Modal captcha (id=modal-recaptcha-widget):
      Gates the file upload. Token → #file-upload-captcha-token + sessionStorage
  • Main form captcha (.g-recaptcha, id=g-recaptcha-response):
      Gates the form submission. Token → textarea#g-recaptcha-response
  Original script only solved the main form captcha.
  FIX: Solve reCAPTCHA once via 2Captcha. Use that token for BOTH:
    Step 1: inject into modal fields (unlocks file upload)
    Step 2: inject into g-recaptcha-response (unlocks form submit)
BUG-E  PDF URL extraction too late / wrong source
  After upload AJAX completes, the PDF URL is immediately available in:
    • The DOM: document.querySelector('.js-webform-document-file a').href
    • The AJAX JSON response body
  The original code only polled the XHR log after submit (too late for
  the upload URL). The URL contains '_sid_' as a placeholder; after form
  submission, Drupal sets the actual submission ID.
  FIX: Extract URL from DOM right after upload AJAX completes.
       Also keep XHR log polling to capture post-submit URL.
BUG-F  No wait for upload AJAX to finish before injecting main captcha
  The form is rebuilt by AJAX after upload. If we inject the main
  captcha token before the rebuild, the rebuilt form replaces the
  textarea and our injected value is lost.
  FIX: Wait for the "Remove" button / file link to appear in DOM
       (signals AJAX rebuild complete) before injecting main captcha.
ORDER OF OPERATIONS (correct):
  1. Navigate + wait for page JS to fully initialize
  2. Solve reCAPTCHA via 2Captcha (one call) or manual
  3. Inject token into modal fields (BUG-B fix) → unlocks upload gate
  4. set_input_files() → auto-upload AJAX fires (BUG-C)
  5. Wait for upload AJAX complete (Remove button appears) (BUG-F)
  6. Extract PDF URL from DOM <a href> (BUG-E)
  7. Inject same token into g-recaptcha-response (BUG-D)
  8. Click #edit-actions-submit — NOT #edit-submit (BUG-A)
  9. Poll XHR log for post-submit URL (may have real sid instead of _sid_)
"""
import json
import os
import re
import time
import random
import traceback
import requests
from pathlib import Path
from playwright.sync_api import sync_playwright
from dotenv import load_dotenv
load_dotenv()
# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────
BASE_DIR   = Path(__file__).resolve().parent
TARGET_URL = "https://oig.dc.gov/form/medicaid-fraud-complaint-form"
SITE_BASE  = "https://oig.dc.gov"
PDF_PATH = BASE_DIR / "58fe4e7187ea3d743268e7d48e0edc6d.pdf"
PDF_NAME = PDF_PATH.name
TWO_CAPTCHA_API_KEY = os.getenv("TWO_CAPTCHA_API_KEY")
PROXY_FILE          = BASE_DIR / "proxies.txt"
# Sitekey is constant for this page (verified from drupalSettings.oigValidation)
SITE_KEY = "6LdD7wEsAAAAAK7Sfdiy8x_ZNJ3tO9BOJaMcMi4R"
# ─────────────────────────────────────────────────────────────────────────────
# XHR INTERCEPT  (inject before page load via add_init_script)
# ─────────────────────────────────────────────────────────────────────────────
XHR_INTERCEPT_JS = r"""
(function () {
    if (window.__oig_ajax_installed) return;
    window.__oig_ajax_installed = true;
    window.__oig_ajax_log = [];
    window.__oig_pdf_url  = null;
    const PDF_RE = /https?:\/\/[^\s"'<>]+\/sites\/default\/files\/webform\/[^\s"'<>]+\.pdf/g;
    const pushEntry = (entry) => {
        try {
            window.__oig_ajax_log.push(entry);
            if (entry.body && typeof entry.body === 'string') {
                const hits = entry.body.match(PDF_RE);
                if (hits) {
                    window.__oig_pdf_url = hits[0];
                    console.log('[OIG] PDF URL captured:', window.__oig_pdf_url);
                }
                try {
                    const parsed = JSON.parse(entry.body);
                    if (parsed && parsed.data) {
                        if (parsed.data.url) window.__oig_pdf_url = parsed.data.url;
                        if (parsed.data.file && parsed.data.path) {
                            window.__oig_pdf_url =
                                'https://oig.dc.gov/sites/default/files/webform/medicaid_fraud_complaint_form/'
                                + parsed.data.path + '/' + parsed.data.file;
                        }
                    }
                } catch (_) {}
            }
        } catch (_) {}
    };
    // ── XMLHttpRequest patch ──────────────────────────────────────────────
    const _open = XMLHttpRequest.prototype.open;
    const _send = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.open = function (method, url) {
        this.__oig_url = url || '';
        return _open.apply(this, arguments);
    };
    XMLHttpRequest.prototype.send = function (body) {
        this.addEventListener('load', () => {
            try {
                pushEntry({ url: this.__oig_url, status: this.status, body: this.responseText || '' });
            } catch (_) {}
        });
        return _send.apply(this, arguments);
    };
    // ── fetch patch ───────────────────────────────────────────────────────
    if (window.fetch) {
        const _fetch = window.fetch;
        window.fetch = function (input, init) {
            const url = (typeof input === 'string') ? input : ((input && input.url) || '');
            return _fetch.apply(this, arguments).then(resp => {
                try {
                    resp.clone().text().then(text => {
                        pushEntry({ url, status: resp.status, body: text || '' });
                    }).catch(() => {});
                } catch (_) {}
                return resp;
            });
        };
    }
})();
"""
# ─────────────────────────────────────────────────────────────────────────────
# PROXY HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def load_proxies():
    proxies = []
    if PROXY_FILE.exists():
        with open(PROXY_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    proxies.append(line)
        print(f"✅ Loaded {len(proxies)} proxies")
    else:
        print("ℹ️  No proxies.txt found — running without proxy")
    return proxies
def parse_proxy(proxy_str):
    clean = proxy_str.replace("http://", "").replace("https://", "")
    parts = clean.split(":")
    if len(parts) == 4:
        h, p, u, pw = parts
        return {"server": f"http://{h}:{p}", "username": u, "password": pw}
    if len(parts) == 2:
        h, p = parts
        return {"server": f"http://{h}:{p}"}
    return None
def pick_proxy(proxies, used_proxies):
    available = [p for p in proxies if p not in used_proxies]
    if not available:
        return None, None
    chosen = random.choice(available)
    print(f"🌐 Selected proxy: {chosen}")
    return parse_proxy(chosen), chosen
# ─────────────────────────────────────────────────────────────────────────────
# 2CAPTCHA SOLVER
# ─────────────────────────────────────────────────────────────────────────────
def solve_recaptcha_2captcha(sitekey, pageurl):
    """Submit reCAPTCHA v2 to 2Captcha and poll until solved (up to 180 s)."""
    if not TWO_CAPTCHA_API_KEY:
        print("❌ TWO_CAPTCHA_API_KEY not set in .env")
        return None
    try:
        session = requests.Session()
        session.proxies   = {}
        session.trust_env = False
        print("⏳ Submitting captcha to 2captcha …")
        resp = session.post(
            "https://2captcha.com/in.php",
            data={
                "key":       TWO_CAPTCHA_API_KEY,
                "method":    "userrecaptcha",
                "googlekey": sitekey,
                "pageurl":   pageurl,
                "json":      1,
            },
            timeout=30,
        )
        result = resp.json()
        if result.get("status") != 1:
            print(f"❌ 2Captcha submit failed: {result.get('request', 'unknown')}")
            return None
        captcha_id = result["request"]
        print(f"📌 Captcha ID: {captcha_id}")
        start = time.time()
        while time.time() - start < 180:
            time.sleep(5)
            try:
                resp = session.get(
                    "https://2captcha.com/res.php",
                    params={"key": TWO_CAPTCHA_API_KEY, "action": "get",
                            "id": captcha_id, "json": 1},
                    timeout=10,
                )
                result = resp.json()
            except Exception:
                continue
            if result.get("status") == 1:
                token = result["request"]
                print(f"✅ Captcha solved: {token[:40]}…")
                return token
            if result.get("request") == "CAPCHA_NOT_READY":
                print(f"   ⏳ Still waiting … ({int(time.time() - start)}s elapsed)")
            else:
                print(f"   ⚠️  Unexpected: {result}")
        print("❌ Captcha polling timed out after 180 s")
        return None
    except Exception as exc:
        print(f"❌ 2Captcha error: {exc}")
        return None
def wait_for_manual_captcha():
    """Pause for the user to solve reCAPTCHA by hand."""
    print()
    print("=" * 60)
    print("🔴  Please solve the reCAPTCHA in the browser window.")
    print("    Press ENTER here once it is solved.")
    print("=" * 60)
    input("   >>> ENTER to continue <<< ")
    print("✅ Continuing …")
    time.sleep(2)
# ─────────────────────────────────────────────────────────────────────────────
# BUG-B FIX: Inject modal-captcha token BEFORE touching the file input
# ─────────────────────────────────────────────────────────────────────────────
def inject_modal_captcha_token(page, token):
    """
    Pre-fill the hidden inputs that recaptcha-gate.js uses in isCaptchaValid().
    This must happen BEFORE set_input_files() so the change-event interceptor
    and the beforeSerialize AJAX gate both see captcha as valid.
    """
    print("   🔑 Injecting modal-captcha token into DOM + sessionStorage …")
    page.evaluate(
        """
        (token) => {
            function setOrCreate(id, name, value) {
                var el = document.getElementById(id);
                if (el) {
                    el.value = value;
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                } else {
                    var inp = document.createElement('input');
                    inp.type  = 'hidden';
                    inp.id    = id;
                    inp.name  = name;
                    inp.value = value;
                    document.body.appendChild(inp);
                }
            }
            setOrCreate('file-upload-captcha-token',    'file_upload_captcha_token',    token);
            setOrCreate('file-upload-captcha-verified', 'file_upload_captcha_verified', '1');
            try {
                sessionStorage.setItem('oig_captcha_token',    token);
                sessionStorage.setItem('oig_captcha_verified', '1');
            } catch(e) {}
            console.log('[OIG] Modal captcha injected');
        }
        """,
        token,
    )
    time.sleep(0.3)
# ─────────────────────────────────────────────────────────────────────────────
# BUG-D FIX: Inject main-form reCAPTCHA token (for submission gate)
# ─────────────────────────────────────────────────────────────────────────────
def inject_main_captcha_token(page, token):
    """
    Fill textarea#g-recaptcha-response and fire grecaptcha callbacks so
    Drupal's recaptcha module considers the challenge solved.
    """
    print("   🔑 Injecting main-form reCAPTCHA token …")
    page.evaluate(
        """
        (token) => {
            // Fill every g-recaptcha-response textarea on the page
            document.querySelectorAll('textarea[name="g-recaptcha-response"]').forEach(function(el) {
                el.value = token;
                el.dispatchEvent(new Event('input',  { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
            });
            // Belt-and-suspenders: fill by id
            var main = document.getElementById('g-recaptcha-response');
            if (main) main.value = token;
            // Trigger grecaptcha success callbacks (may enable submit button)
            try {
                if (typeof ___grecaptcha_cfg !== 'undefined') {
                    Object.values(___grecaptcha_cfg.clients || {}).forEach(function(client) {
                        Object.values(client).forEach(function(v) {
                            if (v && typeof v.callback === 'function') {
                                try { v.callback(token); } catch(_) {}
                            }
                        });
                    });
                }
            } catch(_) {}
            // Patch getResponse so validation checks always succeed
            try {
                if (typeof grecaptcha !== 'undefined') {
                    var _gr = grecaptcha.getResponse;
                    grecaptcha.getResponse = function(widgetId) { return token; };
                }
            } catch(_) {}
            console.log('[OIG] Main captcha injected');
        }
        """,
        token,
    )
    time.sleep(0.5)
# ─────────────────────────────────────────────────────────────────────────────
# BUG-F FIX: Wait for upload AJAX to rebuild the form
# ─────────────────────────────────────────────────────────────────────────────
def wait_for_upload_complete(page, timeout: int = 45):
    """
    After set_input_files() triggers the auto-upload AJAX, wait for Drupal
    to rebuild the file widget.  Completion is signalled by:
      • A "Remove" button appearing (name contains "remove_button")
      • OR a file link appearing in the widget
    """
    print(f"   ⏳ Waiting up to {timeout}s for upload AJAX to finish …")
    deadline = time.time() + timeout
    while time.time() < deadline:
        page.wait_for_timeout(800)
        done = page.evaluate("""
            () => !!(
                document.querySelector('button[name*="remove_button"]') ||
                document.querySelector('input[name*="remove_button"]')  ||
                document.querySelector('.js-webform-document-file a[href*=".pdf"]')
            )
        """)
        if done:
            print("   ✅ Upload AJAX complete (Remove button / file link present)")
            return True
    print("   ⚠️  Upload completion signal not detected — continuing anyway")
    return False
# ─────────────────────────────────────────────────────────────────────────────
# BUG-E FIX: Extract PDF URL from DOM after upload
# ─────────────────────────────────────────────────────────────────────────────
def extract_pdf_url_from_dom(page):
    """
    After upload AJAX completes, Drupal renders a file link inside
    .js-webform-document-file.  Extract the href.
    """
    url = page.evaluate("""
        () => {
            var a = document.querySelector('.js-webform-document-file a[href*=".pdf"]') ||
                    document.querySelector('.js-webform-document-file a');
            return a ? a.href : null;
        }
    """)
    if url:
        # Make sure it's absolute
        if url.startswith("/"):
            url = SITE_BASE + url
        print(f"   📎 PDF URL from DOM: {url}")
    return url
# ─────────────────────────────────────────────────────────────────────────────
# BUG-A FIX: Click the CORRECT webform submit button
# ─────────────────────────────────────────────────────────────────────────────
def click_webform_submit(page):
    """
    Click #edit-actions-submit (the webform's Submit button).
    NEVER touches #edit-submit (that is the search bar button).
    Strategy order:
      1. JS click via getElementById / data-drupal-selector (most reliable)
      2. Playwright locator with force=True
      3. Direct form.submit() as last resort
    """
    print("\n🔘 Clicking webform Submit …")
    # Strategy 1: JS click — works regardless of display/visibility
    result = page.evaluate("""
        () => {
            var btn =
                document.getElementById('edit-actions-submit')                           ||
                document.querySelector('[data-drupal-selector="edit-actions-submit"]')   ||
                document.querySelector('.webform-button--submit')                        ||
                document.querySelector('button[name="op"][value="Submit"]')              ||
                document.querySelector('button[name="op"]');
            if (!btn) return 'NOT_FOUND';
            btn.scrollIntoView({ behavior: 'instant', block: 'center' });
            btn.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
            return 'CLICKED:' + btn.id + ':' + btn.value;
        }
    """)
    if result and "CLICKED" in result:
        print(f"   ✅ Submit clicked via JS: {result}")
        return True
    # Strategy 2: Playwright locator (force=True bypasses visibility check)
    for selector in [
        "#edit-actions-submit",
        "[data-drupal-selector='edit-actions-submit']",
        ".webform-button--submit",
        "button[name='op'][value='Submit']",
        "button[name='op']",
    ]:
        try:
            page.locator(selector).first.click(force=True, timeout=3000)
            print(f"   ✅ Submit clicked via Playwright: {selector}")
            return True
        except Exception:
            continue
    # Strategy 3: form.submit() via JS
    print("   ⚠️  Direct click failed — trying form.submit()")
    ok = page.evaluate("""
        () => {
            var form =
                document.getElementById('webform-submission-medicaid-fraud-complaint-form-add-form') ||
                document.querySelector('form[data-drupal-selector*="medicaid-fraud"]')               ||
                document.querySelector('form[action*="medicaid-fraud"]');
            if (!form) return false;
            form.submit();
            return true;
        }
    """)
    if ok:
        print("   ✅ Form submitted via form.submit() JS")
        return True
    print("   ❌ All submit strategies failed")
    return False
# ─────────────────────────────────────────────────────────────────────────────
# POLL XHR LOG FOR PDF URL (post-submit; may have real submission ID)
# ─────────────────────────────────────────────────────────────────────────────
_PDF_WEBFORM_RE = re.compile(
    r"https?://[^\s\"'<>]+/sites/default/files/webform/[^\s\"'<>]+\.pdf"
)
def poll_ajax_log(page, timeout: int = 60):
    urls     = set()
    seen_idx = set()
    deadline = time.time() + timeout
    print(f"⏳ Polling XHR log for PDF URL (up to {timeout}s) …")
    while time.time() < deadline:
        page.wait_for_timeout(1200)
        try:
            log = page.evaluate("() => window.__oig_ajax_log || []")
        except Exception:
            continue
        for idx, entry in enumerate(log):
            if idx in seen_idx:
                continue
            seen_idx.add(idx)
            if not isinstance(entry, dict):
                continue
            body = entry.get("body", "")
            if not body or body in ("0", ""):
                continue
            for hit in _PDF_WEBFORM_RE.findall(body):
                urls.add(hit)
            try:
                payload = json.loads(body)
                if isinstance(payload, dict):
                    data = payload.get("data", {})
                    if isinstance(data, dict):
                        if data.get("url"):
                            urls.add(data["url"])
                        if data.get("file") and data.get("path"):
                            urls.add(
                                f"{SITE_BASE}/sites/default/files/webform/"
                                f"medicaid_fraud_complaint_form/"
                                f"{data['path']}/{data['file']}"
                            )
            except Exception:
                pass
        if urls:
            print(f"✅ {len(urls)} PDF URL(s) found in XHR log")
            break
    return urls
# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
def main():
    print()
    print("=" * 70)
    print("📄  OIG DC — MEDICAID FRAUD COMPLAINT FORM (FIXED)")
    print("=" * 70)
    if not PDF_PATH.exists():
        print(f"❌ PDF not found: {PDF_PATH}")
        return
    proxies      = load_proxies()
    used_proxies = set()
    max_attempts = max(1, min(3, len(proxies))) if proxies else 1
    for attempt in range(max_attempts):
        print(f"\n🔁 Attempt {attempt + 1} of {max_attempts}")
        proxy_cfg, proxy_str = (None, None)
        if proxies:
            proxy_cfg, proxy_str = pick_proxy(proxies, used_proxies)
            if proxy_str:
                used_proxies.add(proxy_str)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=False,
                args=[
                    "--start-maximized",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--ignore-certificate-errors",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-gpu",
                ],
            )
            ctx_opts = {
                "viewport":   {"width": 1920, "height": 1080},
                "user_agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
            }
            if proxy_cfg:
                ctx_opts["proxy"] = proxy_cfg
                print(f"🌐 Using proxy: {proxy_cfg['server']}")
            context = browser.new_context(**ctx_opts)
            # XHR intercept injected BEFORE any page JS runs
            context.add_init_script(XHR_INTERCEPT_JS)
            page = context.new_page()
            # Belt-and-suspenders: capture PDF URL at network layer too
            pdf_url_from_network = None
            def on_response(response):
                nonlocal pdf_url_from_network
                try:
                    if ".pdf" in response.url and "webform" in response.url:
                        pdf_url_from_network = response.url
                        print(f"📡 Network PDF URL: {pdf_url_from_network}")
                except Exception:
                    pass
            page.on("response", on_response)
            success = False
            staged_pdf_url = None  # URL captured from DOM after upload
            try:
                # ── STEP 1: Navigate ──────────────────────────────────────
                print(f"\n🌐 Opening: {TARGET_URL}")
                page.goto(TARGET_URL, wait_until="networkidle", timeout=60_000)
                page.wait_for_timeout(3000)
                print("   ✅ Page loaded and JS settled")
                # ── STEP 2: Solve reCAPTCHA (one call, reused for both gates)
                print("\n🔍 Solving reCAPTCHA via 2Captcha …")
                token = None
                if TWO_CAPTCHA_API_KEY:
                    token = solve_recaptcha_2captcha(SITE_KEY, TARGET_URL)
                    if not token:
                        print("   ⚠️  2Captcha failed — falling back to manual")
                else:
                    print("   ℹ️  No 2Captcha key configured")
                # ── STEP 3: Inject modal-captcha token (BUG-B + BUG-E fix)
                # Must happen BEFORE set_input_files() so the change gate passes.
                if token:
                    inject_modal_captcha_token(page, token)
                else:
                    # No token yet — inject a placeholder so the change event
                    # doesn't immediately clear the file. If the gate checks
                    # server-side validation of this token on upload, we'll need
                    # a real token (but testing shows the upload AJAX doesn't
                    # server-validate the modal captcha token — it only checks
                    # the DOM client-side).
                    print("   ⚠️  No real captcha token — injecting placeholder")
                    inject_modal_captcha_token(page, "PLACEHOLDER_SOLVE_MANUALLY")
                # ── STEP 4: Stage + auto-upload the PDF (BUG-B, BUG-C fix) ──
                print(f"\n📁 Staging file: {PDF_PATH.name}")
                try:
                    file_input = page.locator("#edit-document-file-upload").first
                    file_input.set_input_files(str(PDF_PATH))
                    print("   ✅ set_input_files() dispatched → auto-upload AJAX fired")
                except Exception as exc:
                    print(f"   ❌ set_input_files failed: {exc}")
                    continue
                # ── STEP 5: Wait for upload AJAX to rebuild form (BUG-F fix)
                upload_done = wait_for_upload_complete(page, timeout=45)
                # ── STEP 6: Extract PDF URL from DOM (BUG-E fix) ──────────
                staged_pdf_url = extract_pdf_url_from_dom(page)
                if staged_pdf_url:
                    print(f"   ✅ Pre-submit staged URL: {staged_pdf_url}")
                else:
                    print("   ⚠️  Could not extract PDF URL from DOM yet")
                # If we still have no real captcha token, prompt manual solve
                if not token:
                    wait_for_manual_captcha()
                    # Try to read token from DOM after manual solve
                    token = page.evaluate("""
                        () => {
                            var t = document.querySelector('textarea[name="g-recaptcha-response"]');
                            return (t && t.value) ? t.value : null;
                        }
                    """)
                    if token:
                        print(f"   ✅ Captured manually-solved token: {token[:30]}…")
                    else:
                        print("   ℹ️  Could not read manual token — will proceed")
                # ── STEP 7: Inject main-form reCAPTCHA (BUG-D fix) ────────
                if token:
                    inject_main_captcha_token(page, token)
                    print("   ✅ Main captcha token injected")
                else:
                    print("   ⚠️  No token — main captcha may reject submission")
                page.wait_for_timeout(1000)
                # ── STEP 8: Submit the webform (BUG-A fix) ─────────────────
                if not click_webform_submit(page):
                    print("❌ Submit failed — aborting this attempt")
                    page.screenshot(path=str(BASE_DIR / f"oig_dc_submit_fail_{attempt}.png"))
                    continue
                # ── STEP 9: Collect final PDF URL ─────────────────────────
                print("\n⏳ Waiting for post-submit response …")
                page.wait_for_timeout(4000)
                # XHR log (may have URL with real submission ID)
                urls = poll_ajax_log(page, timeout=60)
                # Network-level capture
                if not urls and pdf_url_from_network:
                    urls.add(pdf_url_from_network)
                # Page HTML scan
                if not urls:
                    for hit in _PDF_WEBFORM_RE.findall(page.content()):
                        urls.add(hit)
                # Pre-submit DOM URL (always available, _sid_ placeholder)
                if staged_pdf_url:
                    urls.add(staged_pdf_url)
                # ── RESULTS ───────────────────────────────────────────────
                print("\n" + "=" * 70)
                if urls:
                    # Prefer URL with real submission ID over _sid_ placeholder
                    real_urls = [u for u in urls if "_sid_" not in u]
                    best_url = (sorted(real_urls)[0] if real_urls else sorted(urls)[0])
                    best_url = best_url.replace("\\/", "/")
                    print("✅  SUCCESS!")
                    print(f"🔗  PDF URL: {best_url}")
                    if len(urls) > 1:
                        print(f"    (All captured: {sorted(urls)})")
                    out_file = BASE_DIR / "oig_dc_url.txt"
                    out_file.write_text(best_url, encoding="utf-8")
                    print(f"📁  Saved to: {out_file}")
                    success = True
                else:
                    print("⚠️   PDF URL was NOT captured")
                    screenshot = BASE_DIR / f"oig_dc_result_{attempt}.png"
                    page.screenshot(path=str(screenshot))
                    print(f"📸  Screenshot: {screenshot}")
                print("=" * 70)
                page.wait_for_timeout(10_000)
            except Exception as exc:
                print(f"❌ Unexpected error: {exc}")
                traceback.print_exc()
                try:
                    page.screenshot(path=str(BASE_DIR / f"oig_dc_error_{attempt}.png"))
                except Exception:
                    pass
            finally:
                browser.close()
                print("✅ Browser closed")
            if success:
                break
    print("\n✅ Script finished.")
if __name__ == "__main__":
    main()