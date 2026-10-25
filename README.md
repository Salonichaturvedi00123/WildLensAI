# WildLensAI — Agentic Wildlife Identification, Conversational Assistant & Payment Integration

**WildLensAI** is an evidence-grounded wildlife identification, interactive educational assistant, and digital wallet platform engineered with **Django 5.1+**, **SQLite**, **LangGraph StateGraph**, **Google Gemini Vision**, **Razorpay Test Mode**, and **Django Unfold**.

Designed for local development and B.Tech college viva demonstration, it allows users to photograph wildlife specimens, identify species with diagnostic visual reasoning, verify taxonomy and biology through live external biological registries (GBIF & MediaWiki), interact with a floating, draggable, and resizable AI assistant via voice or text in English and Hindi, and purchase identification credits through Razorpay Test Mode checkout with server-side cryptographic verification.

---

## 1. Project Overview

WildLensAI combines agentic multimodal artificial intelligence with enterprise-grade web architecture:
- **Multimodal Vision Diagnostics:** Identifies species using Google Gemini Vision with Pydantic structured output, extracting observable visual clues (coat patterns, crest shape, limb geometry), alternative look-alikes, and diagnostic notes.
- **Agentic StateGraph:** Evaluates diagnostic certainty. For identified species, a cyclic LangGraph agent dynamically queries authoritative biodiversity databases (**GBIF Species API** and **MediaWiki**).
- **Floating, Draggable, Resizable Assistant:** Built with **interact.js**, allowing users on desktop and laptop screens to dock the assistant in the sidebar, undock it into a floating window, drag it freely, resize it within viewport bounds, minimize it to a corner launcher, or maximize it for full-screen analysis. Automatically falls back to an offcanvas drawer on mobile and tablet screens.
- **Voice Speech Recognition & Playback:** Uses native browser **Web Speech API** (`SpeechRecognition` / `webkitSpeechRecognition`) for microphone speech-to-text with bilingual support (English `en-IN` and Hindi `hi-IN`), coupled with native **SpeechSynthesis** for voice playback with auto-read and speed regulation (0.75x to 1.5x).
- **Razorpay Sandbox Payment Integration:** Features official **Razorpay Standard Checkout (checkout.js)** and **Razorpay Python SDK** in Test Mode (`RAZORPAY_MODE=test`). Implements strict server-side HMAC-SHA256 signature verification, remote REST capture verification, webhook event logging, localhost reconciliation, and atomic, idempotent wallet updates.
- **Modern Unfold Admin & Analytics Dashboard:** Powered by **Django Unfold**, featuring a real-time analytics dashboard driven by SQLite ORM aggregations (`Count`, `Sum`, `TruncDate`), date range filtering (`7d`, `30d`, `all`), 5 interactive Chart.js visualizations, and separated Razorpay sandbox volumes.

---

## 2. Architecture & Design Principles

WildLensAI is architected as a robust, single-server monolithic application suitable for academic evaluation:
1. **No Over-Engineering:** Strictly avoids microservices, Docker, React, Next.js, Celery, Redis, or vector database servers. All persistence lives in embedded SQLite.
2. **Evidence-Grounded AI:** Replaces ungrounded LLM hallucinations with live, read-only scientific tools (GBIF Taxonomic Registry & MediaWiki).
3. **Defense-in-Depth Security:**
   - Secrets remain strictly on the backend (`.env`).
   - Payment order verification never trusts frontend success callbacks or client-provided pricing.
   - HMAC-SHA256 verification via the Razorpay Python SDK with remote REST API confirmation before any balance is modified.
   - Database operations use atomic transactions and conditional Django ORM `F()` expressions to prevent race conditions and negative balances.
   - Idempotency guarantees protect against replay attacks on checkout and webhook deliveries.
4. **Professional UI System:** High-contrast nature-themed design built on Bootstrap 5.3.3 and official Bootstrap Icons (`bi bi-*`). Zero decorative emojis used.

---

## 3. Technology Stack

| Layer | Technology | Version | Purpose |
| :--- | :--- | :--- | :--- |
| **Backend Framework** | Django | `>=5.1.0` | Monolithic web application, ORM, Auth, Admin, Sessions |
| **Relational Database**| SQLite 3 | Embedded | Serverless local ACID persistence |
| **Admin UI** | Django Unfold | `>=0.110.0` | Modern Tailwind-inspired management dashboard & navigation |
| **AI Multimodal Vision** | Google Gemini Vision | `langchain-google-genai>=2.0.0` | Multimodal visual identification & structured extraction |
| **Agentic Workflow** | LangGraph | `langgraph>=0.2.0` | Cyclic StateGraph, ToolNode, and execution limits |
| **Payment Gateway** | Razorpay Python SDK | `razorpay>=1.4.0` | Server-side order creation, HMAC verification, API fetch |
| **Payment Frontend** | Razorpay Standard Checkout | `checkout.js` | Modal checkout in Test Mode |
| **Window Drag & Resize**| interact.js | `1.10.27` | Floating assistant dragging and resizing |
| **Image Validation** | Pillow (PIL) | `>=10.0.0` | In-memory image safety, mime verification, size checks |
| **HTTP Transport** | HTTPX | `>=0.28.0` | Resilient external API requests with strict timeouts |
| **Markdown Rendering** | markdown-it-py | `>=3.0.0` | Safe HTML rendering of wildlife dossiers and chat replies |
| **Frontend Framework** | Bootstrap | `5.3.3` | Responsive layout, flexbox grids, modals, offcanvas |
| **Iconography** | Bootstrap Icons | `1.13.2` | Semantic iconography across web views |
| **Admin Iconography** | Google Material Symbols | CDN | Navigation and metric cards in Unfold Admin |

---

## 4. Installation & Local Setup

### Step 1: Clone or Open Repository
```powershell
cd "r:\WildLence AI\WildLensAI"
```

### Step 2: Set Up Virtual Environment
```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

### Step 3: Install Dependencies
```powershell
pip install -r requirements.txt
```

---

## 5. Environment Configuration

Copy `.env.example` to create your active `.env` file:
```powershell
Copy-Item .env.example .env
```

Configure the environment variables in `.env`:
```ini
# Django Secret Key
SECRET_KEY=django-insecure-dev-fallback-key-for-local-wildlens

# Debug Mode (True for local development)
DEBUG=True

# Allowed Hosts (Comma-separated hostnames or IP addresses)
ALLOWED_HOSTS=127.0.0.1,localhost

# CSRF Trusted Origins (Optional: comma-separated origins for mobile testing over LAN or HTTPS tunnels)
# Example: CSRF_TRUSTED_ORIGINS=https://your-tunnel-name.ngrok-free.app,http://192.168.1.100:8000
CSRF_TRUSTED_ORIGINS=

# Google Gemini API Key (Required for Vision & LangGraph Agent)
# Obtain from Google AI Studio: https://aistudio.google.com/
GOOGLE_API_KEY=your_gemini_api_key_here

# Gemini Model Selection & Timeout
GEMINI_MODEL=gemini-2.5-flash
GEMINI_TIMEOUT_SECONDS=30

# Razorpay Test Mode Configuration (Required for Wallet & Checkout)
# Obtain Test API Keys from Razorpay Dashboard: https://dashboard.razorpay.com/app/keys
RAZORPAY_KEY_ID=rzp_test_your_test_key_here
RAZORPAY_KEY_SECRET=your_test_secret_here
RAZORPAY_WEBHOOK_SECRET=your_webhook_secret_here
RAZORPAY_MODE=test
```

---

## 6. Database Setup & Migrations

WildLensAI uses SQLite out-of-the-box with pre-configured migrations:

```powershell
# Verify migration integrity
python manage.py makemigrations --check --dry-run

# Apply all database migrations
python manage.py migrate

# Create a staff superuser for Django Admin
python manage.py createsuperuser
```

Default active credit packages (Starter ₹49 / 50 credits, Explorer ₹99 / 120 credits, Wildlife Pro ₹199 / 300 credits) are automatically provisioned by data migration.

---

## 7. Floating, Draggable, Resizable AI Assistant

The assistant panel on the animal prediction result page (`/predictions/<id>/`) offers four window modes on desktop/laptop displays:

- **Docked Mode (Default):** Occupies the right column beside the Grounded Wildlife Dossier. Uses CSS sticky positioning with smooth natural scrolling.
- **Floating Mode:** Undocks into an independent floating window via `interact.js`:
  - **Drag Handle:** Grab and move via the chat header (`chat-header-drag`). Controls and buttons are excluded to prevent accidental dragging.
  - **Resize Handle:** Drag from the bottom-right corner resize handle (`chat-resize-handle`) to adjust dimensions (min width: 340px, min height: 420px; clamped within viewport boundaries).
  - **Text Selection Protection:** Text selection is preserved inside message bubbles and composer; drag events do not trigger text highlighting.
- **Maximized Mode:** Expands the assistant into a focused modal workspace (92vw width, full viewport height) for detailed reading and dialogue.
- **Minimized Mode:** Collapses the window into a compact floating launcher button in the bottom-right corner, displaying an unread message counter badge.
- **Reset Action:** Click the reset button to restore default sidebar dimensions and position.
- **Persistence:** Layout coordinates, dimensions, and window mode preferences are stored in the browser's `localStorage` (`wildlens_assistant_prefs`) across page reloads.
- **Responsive Tablet/Mobile Fallback:** Automatically switches to an accessible offcanvas drawer (`#assistantOffcanvas`) with a floating launcher button on screens narrower than 992px.

---

## 8. Voice Speech Recognition & Playback

WildLensAI features zero-cost browser-native speech integration without external paid speech services:

### Voice Input (Speech-to-Text)
- **API:** Native `window.SpeechRecognition` or `window.webkitSpeechRecognition`.
- **Languages:** Language selector in composer supporting English (`en-IN`) and Hindi (`hi-IN`).
- **Visual Feedback:** Red pulsing listening badge (`.btn-voice-mic.listening`), interim transcript preview panel, and stop action.
- **Error States:** Graceful handling for permission denial, absence of microphone devices, or unsupported browsers.

### Text-to-Speech Playback (SpeechSynthesis)
- **API:** Native `window.speechSynthesis` and `SpeechSynthesisUtterance`.
- **Per-Message Audio Controls:** Each assistant message includes Play, Pause, Resume, Stop, and Replay action buttons with active speaking pulse indicators.
- **Auto-Read:** Switchable toggle in the chat toolbar to read newly generated AI responses aloud automatically.
- **Playback Speed Selector:** Adjustable playback rate (0.75x, 1.0x, 1.25x, 1.5x).
- **Hindi Script Detection:** Automatically selects appropriate Hindi synthesis voices when Devanagari characters are detected.
- **Audio Conflict Prevention:** Activating microphone speech recognition automatically pauses any active text-to-speech utterance.

---

## 9. Razorpay Test Mode Payment Workflow

The wallet recharge system operates in **Razorpay Sandbox Test Mode** (`RAZORPAY_MODE=test`):

### Credit Purchase Flow
1. **User Selects Package:** User navigates to `/transactions/recharge/` and chooses a credit plan (e.g., Explorer: ₹99 for 120 credits).
2. **Order Creation:** Frontend requests order creation via POST `/transactions/orders/create/`. The backend:
   - Validates user authentication.
   - Fetches the active `CreditPlan` price and credits from SQLite.
   - Calls Razorpay SDK `client.order.create()` with amount in paise.
   - Saves a `PaymentOrder` in `status="created"`.
   - Returns public parameters (`key_id`, `razorpay_order_id`, `amount_paise`, user prefill).
3. **Checkout Modal:** Official Razorpay `checkout.js` modal opens on the page.
4. **Test Payment Details:**
   - **Test Card:** `4111 1111 1111 1111` (any future expiry, CVV: `123`)
   - **Test UPI ID:** `success@razorpay`
   - **Test OTP:** `123456`
5. **Server Verification:** Upon payment completion, frontend sends `razorpay_order_id`, `razorpay_payment_id`, and `razorpay_signature` to POST `/transactions/orders/verify/`:
   - Checks user ownership of the order.
   - Executes cryptographic HMAC verification: `client.utility.verify_payment_signature()`.
   - Remotely fetches payment status via `client.payment.fetch()` to ensure status is `captured` and amount matches expected paise.
   - Inside an atomic transaction (`transaction.atomic()`), updates `PaymentOrder.status = "paid"`.
   - Credits user `Profile.wallet_balance` via `F("wallet_balance") + credits`.
   - Creates an immutable audit record in `WalletTransaction` (`transaction_type="credit_purchase"`).
6. **Idempotency & Replay Protection:** If the verification request is re-submitted, the system detects `order.status == "paid"` and returns success without double-crediting.
7. **Webhook Handler:** Dedicated webhook endpoint (`/transactions/webhooks/razorpay/`) verifies `X-Razorpay-Signature` HMAC, deduplicates by `event_id`, and processes `payment.captured` or `payment.failed` events.
8. **Reconciliation:** Fallback reconciliation endpoint (`/transactions/orders/<id>/reconcile/`) allows manual verification of captured orders during local testing.

---

## 10. Modern Django Unfold Admin & Analytics

WildLensAI replaces the standard Django admin with **Django Unfold**:

- **Portal URL:** `/admin/` (Accessible only by staff superusers).
- **Date Range Filters:** Switch analytics time horizons with a single click:
  - `?period=7d` (Last 7 Days)
  - `?period=30d` (Last 30 Days)
  - `?period=all` (All Time)
- **KPI Metrics Cards:**
  - **Registered Users:** Total count and new signups in period.
  - **Wildlife Identifications:** Period volume, identified count, and success percentage.
  - **AI Wildlife Assistant:** Total conversations and chat messages exchanged.
  - **Razorpay Sandbox Volume:** Clear **"Razorpay Test Mode Active"** banner displaying captured test orders and simulated volume in INR.
- **Chart.js Visualizations:**
  1. *User Registrations Trend:* Daily user signups.
  2. *Wildlife Identifications Volume:* Daily classification volume.
  3. *Top Identified Wildlife Species:* Top 5 species identified.
  4. *Razorpay Test Payment Activity:* Daily captured test orders.
  5. *Identification Outcomes Breakdown:* Identified vs. Uncertain vs. Non-Animal images.
- **Administrative Actions:**
  - `grant_5_demo_credits` & `grant_10_demo_credits`: Batch grant demo credits directly to selected users with audit logging in `WalletTransaction`.
  - Payment orders and webhooks have `has_add_permission = False` and `has_delete_permission = False` to preserve financial audit integrity.

---

## 11. Local Development & Phone Testing

WildLensAI runs locally with SQLite. Test on mobile devices using one of two documented methods:

### Method A: Same Wi-Fi Network (LAN)
1. Ensure your laptop and smartphone are connected to the same Wi-Fi router.
2. Find your laptop's private IPv4 address in PowerShell:
   ```powershell
   ipconfig
   # Look for "IPv4 Address", e.g., 192.168.1.15
   ```
3. Add your private IP to `ALLOWED_HOSTS` in `.env`:
   ```ini
   ALLOWED_HOSTS=127.0.0.1,localhost,192.168.1.15
   ```
4. Start Django binding to all interfaces:
   ```powershell
   python manage.py runserver 0.0.0.0:8000
   ```
5. On your phone, open Chrome or Safari and visit: `http://192.168.1.15:8000/`.
6. **Windows Firewall Note:** If the connection times out, allow Python through the Windows Defender Firewall:
   ```powershell
   New-NetFirewallRule -DisplayName "Django Local Dev 8000" -Direction Inbound -LocalPort 8000 -Protocol TCP -Action Allow
   ```

### Method B: Optional HTTPS Tunnel (For Mobile Microphone Testing)
Modern mobile browsers (Chrome on Android, Safari on iOS) restrict microphone access (`getUserMedia` / `SpeechRecognition`) to **secure origins (`https://` or `localhost`)**. To test voice speech recognition on a phone:
1. Start an HTTPS tunnel (using ngrok or localtunnel):
   ```powershell
   ngrok http 8000
   ```
2. Copy the generated HTTPS forwarding domain (e.g., `https://wildlens-test.ngrok-free.app`).
3. Update `.env` with the tunnel domain:
   ```ini
   ALLOWED_HOSTS=127.0.0.1,localhost,wildlens-test.ngrok-free.app
   CSRF_TRUSTED_ORIGINS=https://wildlens-test.ngrok-free.app
   ```
4. On your smartphone, navigate to `https://wildlens-test.ngrok-free.app/`. Microphone permissions will now prompt and work natively.

---

## 12. Automated Test Suite

WildLensAI maintains an automated test suite across all apps with **178 passing tests**:

```powershell
# Run the complete test suite
python manage.py test

# Run transactions tests (55 tests: Auth, Demo Wallet, Razorpay Checkout, Webhooks, Admin Analytics, Phase 5 E2E)
python manage.py test transactions

# Run animal tests (123 tests: Vision, LangGraph Agent, Pillow Safety, Tools, History, Multilingual)
python manage.py test animal
```

### Verification Highlights:
- **System Check:** `python manage.py check` passes with 0 errors and 0 warnings.
- **Migrations Check:** `python manage.py makemigrations --check --dry-run` confirms clean schema synchronization.
- **Cryptographic Security:** Tests cover signature mismatches, tampered amounts, foreign currency rejection, cross-user isolation, and replay idempotency.
- **Zero-Division Resilience:** Analytics dashboard tested against empty tables.

---

## 13. Step-by-Step Viva Demonstration Walkthrough

Follow this script for a 5-minute academic demonstration:

1. **User Registration & Signup Bonus:**
   - Register a new account at `/transactions/register/`.
   - Log in. The dashboard displays an initial ₹5.00 demo credit grant with a corresponding `signup_bonus` entry in the transaction ledger.
2. **Species Identification & Dossier:**
   - Go to `/predict/`. Upload a wildlife image (e.g., Bengal Tiger).
   - Gemini Vision extracts species, visual field marks, and confidence.
   - LangGraph queries GBIF for scientific taxonomy and MediaWiki for natural history.
   - The result page renders the Grounded Wildlife Dossier with verified source citations.
   - Exactly ₹1.00 is deducted from the wallet.
3. **Floating AI Assistant & Voice Interaction:**
   - On the result page, click the **Undock** button in the assistant header.
   - Drag the assistant window anywhere across the screen.
   - Drag the bottom-right handle to resize the window.
   - Click the microphone button and ask: *"What is this animal's natural habitat?"*
   - Listen to the spoken response using the Play button or toggle Auto-Read.
   - Switch language to Hindi and ask: *"हिंदी में समझाइए"*.
4. **Razorpay Test Mode Credit Recharge:**
   - Navigate to `/transactions/recharge/`.
   - Select the **Explorer Pack (₹99 for 120 credits)**.
   - In the Razorpay modal, select Card: `4111 1111 1111 1111` and OTP `123456`.
   - Upon completion, the wallet instantly reflects ₹124.00, and a new `credit_purchase` entry appears in the audit ledger.
   - Refresh the page to demonstrate that credits are never duplicated.
5. **Modern Admin Panel & Analytics Dashboard:**
   - Navigate to `/admin/` and log in with your superuser credentials.
   - Review the Unfold Dashboard showing real SQLite KPIs and Chart.js graphs.
   - Click the `7d`, `30d`, and `all` period filter buttons to demonstrate live query filtering.
   - Point out the persistent amber **Razorpay Test Mode Sandbox** banner.
   - In the Users list, select a user and run the **Grant 5 Demo Credits** batch action.

---

## 14. Known Limitations & Academic Disclaimers

1. **Academic Test Mode:** WildLensAI operates exclusively in **Razorpay Test Mode**. It is not configured for commercial payment processing or live currency transactions.
2. **Synchronous Execution:** External API requests (Gemini, GBIF, MediaWiki, Razorpay) run within Django's request-response cycle bounded by strict HTTPX timeouts (10s for tools, 30s for Gemini) rather than asynchronous background task queues.
3. **Browser Speech API Requirements:**
   - Web Speech API speech-to-text is supported natively in Google Chrome, Microsoft Edge, and Safari.
   - Mobile browsers require an HTTPS origin (or `localhost`) to grant microphone permissions.
4. **Single Image Analysis:** Predictions evaluate one photograph per submission.
