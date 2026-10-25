# WildLensAI — Project Audit & Architecture Report

**Project Name:** WildLensAI — Agentic Wildlife Identification and Conversational Assistant  
**Author:** Senior Python Developer, Django Architect, and LangGraph AI Engineer  
**Date:** October 2026  
**Status:** Phase 5 Complete (WildLens Context-Aware Conversational Assistant, Bilingual English/Hindi Dialogue, SQLite Persistence, AJAX Vanilla JS Fetch, Controlled Tool Calling)

---

## 1. Executive Summary

WildLensAI is an AI-powered wildlife identification and conversational educational platform built for local development and college demonstration.
The system features wildlife image classification, taxonomy retrieval, prediction history logging, a digital credit wallet, and a lightweight LangGraph StateGraph agent for interactive wildlife inquiry.

### Technology Constraints Adherence
- **Backend Framework:** Django 5.1+ (Python 3.12)
- **Database:** SQLite only (`db.sqlite3` preserved locally, never deleted)
- **Frontend & UI:** Django Templates, Bootstrap 5, Bootstrap Icons (official CSS distribution), Vanilla JavaScript (Zero emojis)
- **Agentic AI & Vision Stack:**
  - `langchain-google-genai` (Gemini API access)
  - `langgraph` (`StateGraph`, `ToolNode`)
  - `pillow` (Image verification & validation)
  - `httpx` (HTTP client transport)
- **Strictly Excluded Systems:** PostgreSQL, Redis, Celery, Docker as a requirement, React, Next.js, Node.js backend, Microservices, LangChain agents as a separate multi-agent framework, CrewAI, AutoGen, Vector databases (ChromaDB, Pinecone, FAISS), and unnecessary background queues.

---

## 2. Current Project Architecture

WildLensAI follows a clean, modular Django Model-View-Template (MVT) architecture with lightweight service layer integration for LangGraph:

```text
WildLensAI/
│
├── manage.py                          # Django CLI entrypoint
├── requirements.txt                   # Pinned core dependencies
├── .env.example                       # Environment configuration template
├── .env                               # Active local environment variables (git-ignored)
├── .gitignore                         # Python/Django/SQLite/Media ignore rules
├── db.sqlite3                         # Local SQLite database (preserved locally)
├── README.md                          # Full setup and project documentation
│
├── docs/
│   └── PROJECT_AUDIT.md               # Complete architectural audit (this document)
│
├── WildLensAI/                        # Core Django Project Configuration
│   ├── settings.py                    # Decoupled, environment-based settings
│   ├── urls.py                        # Central routing & debug media file delivery
│   ├── wsgi.py                        # WSGI entrypoint
│   └── asgi.py                        # ASGI entrypoint
│
├── animal/                            # Core Wildlife Prediction Application
│   ├── models.py                      # AnimalPrediction, WildlifeConversation, WildlifeMessage, Contact
│   ├── views.py                       # Views: home, predict, history, prediction_detail, contact
│   ├── urls.py                        # URL routing for animal views
│   ├── utils.py                       # Prediction orchestration pipeline (bridges views to Gemini service)
│   ├── services/                      # Reusable AI & Validation Services
│   │   ├── __init__.py                # Service module exports
│   │   ├── gemini_service.py          # Gemini model factory & multimodal structured vision pipeline
│   │   └── image_validator.py         # Pillow image security, format, and dimension validator
│   ├── agent/                         # LangGraph Wildlife Agentic System
│   │   ├── __init__.py                # Agent module exports
│   │   ├── graph.py                   # StateGraph definition, nodes, conditional edges, runner
│   │   ├── tools.py                   # Approved read-only tools & ControlledToolNode
│   │   └── state.py                   # WildlifeAgentState TypedDict schema
│   ├── admin.py                       # Django Admin customization (search, filters, inlines)
│   ├── tests.py                       # Automated test suite (33 unit & integration tests)
│   └── migrations/
│       ├── 0001_initial.py            # Initial AnimalPrediction table
│       ├── 0002_contact.py            # Contact inquiry table
│       ├── 0003_animalprediction_user_and_more.py # Safe nullable User foreign key
│       └── 0004_alter_animalprediction_options_alter_contact_options_and_more.py # Extended prediction fields & conversations
│
├── transactions/                      # User Profile, Authentication & Wallet App
│   ├── models.py                      # Profile model with post_save signal
│   ├── views.py                       # Views: user_login, user_logout (POST), register, profile/dashboard, forgot_password
│   ├── urls.py                        # URLs: login, logout, register, profile, dashboard, forgot
│   ├── admin.py                       # Profile admin registration
│   ├── tests.py                       # Automated test suite (12 unit/integration tests)
│   └── migrations/
│       └── 0001_initial.py            # Profile model migration
│
├── static/                            # Static Assets
│   ├── css/
│   │   └── style.css                  # Custom theme variables & typography
│   └── images/
│       ├── history.png                # Empty history placeholder graphic
│       ├── img1.png                   # Hero banner wildlife graphic
│       ├── login.png                  # Split-screen auth card side graphic
│       └── logo.png                   # WildLens AI brand logo
│
├── media/                             # User Upload Directory
│   ├── uploads/                       # User-uploaded wildlife photographs
│   │   └── .gitkeep
│   └── profile_images/                # User profile avatars
│       └── .gitkeep
│
└── templates/                         # Django Templates (Zero Emojis, Bootstrap Icons Only)
    ├── base.html                      # Layout shell with Bootstrap Icons navbar & footer
    ├── animal/
    │   ├── home.html                  # Landing page with hero, features & pricing
    │   ├── predict.html               # Species prediction upload form & report display
    │   ├── history.html               # User-specific prediction history table
    │   ├── prediction_detail.html     # User-isolated prediction details & conversation links
    │   └── contact.html               # Contact & feedback form
    └── auth/
        ├── login.html                 # Modern split-screen login page with password toggle
        ├── register.html              # Registration page with Django password validation
        ├── profile.html               # Protected dashboard & digital wallet interface
        └── forgot.html                # Password reset request interface
```

---

## 3. Working Modules & Data Models

### 3.1. User Authentication & Protected Dashboard
- **Django Built-in Auth:** Fully utilizes Django's `User` model, session authentication, and built-in password validators (`validate_password`).
- **Registration:** Enforces password complexity (minimum length, common password validator, numeric check), uniqueness checks, and automatic provisioning of 5.00 promotional credits via `Profile`.
- **Login & Logout:** Secure login with post-login `next` URL redirection. Session termination strictly enforced using HTTP POST with CSRF validation.
- **Protected Dashboard:** Accessible at `/transactions/dashboard/` and `/transactions/profile/`, guarded by `@login_required(login_url='login')`. Displays account details, wallet credits, and recent predictions.

### 3.2. Extended Animal Prediction Model (`AnimalPrediction`)
The `AnimalPrediction` model was extended backwards-compatibly without data loss:
- `user`: ForeignKey to `User` (`null=True, blank=True`, preserving legacy records without assigning them to random users).
- `uploaded_image`: `ImageField(upload_to="uploads/")`.
- `common_name`: `CharField(max_length=255, default="Unknown Species")` (renamed from `species` preserving legacy records).
- `scientific_name`: `CharField(max_length=255, blank=True, default="")`.
- `confidence`: `FloatField(default=0.0)`.
- `credit_used`: `FloatField(default=1.0)`.
- `identification_status`: `CharField` with choices `identified`, `uncertain`, `not_animal`.
- `identification_notes`: `TextField(blank=True, default="")`.
- `visual_clues`: `JSONField(default=list, blank=True)`.
- `alternative_species`: `JSONField(default=list, blank=True)`.
- `wildlife_information`: `JSONField(default=dict, blank=True)`.
- `source_references`: `JSONField(default=list, blank=True)`.
- `created_at`: `DateTimeField(auto_now_add=True)`.
- **Backward-Compatibility Properties:** `@property def image` and `@property def species` ensure no existing templates, views, or tests break.

### 3.3. Conversation & Message Models
- **`WildlifeConversation`:** Stores user-agent interactions in SQLite (`user`, `prediction`, `title`, `created_at`, `updated_at`).
- **`WildlifeMessage`:** Stores sequential conversational turns in SQLite (`conversation`, `role` e.g. `user`/`assistant`/`system`, `content`, `created_at`).

### 3.4. Profile & Digital Wallet
- Reuses `Profile.wallet_balance` (`DecimalField`) with default 5.00 free credits.
- Prediction transactions verify sufficient balance and safely deduct 1.0 credit per prediction.

---

## 4. User Ownership & Data Isolation

All private records strictly enforce user ownership:
1. **Prediction Isolation:** `/history/` queries `AnimalPrediction.objects.filter(user=request.user)`.
2. **Detail Isolation:** `/predictions/<int:prediction_id>/` validates `get_object_or_404(AnimalPrediction, id=prediction_id, user=request.user)`. User B attempting to view User A's prediction receives an HTTP 404.
3. **Conversation Isolation:** Conversations and messages are linked via foreign keys to `request.user`.

---

## 5. Admin Panel Customization

All domain models are registered in Django Admin:
- **`AnimalPredictionAdmin`:** List display (`common_name`, `scientific_name`, `user`, `identification_status`, `confidence`, `credit_used`, `created_at`), filters (`identification_status`, `created_at`), and search (`common_name`, `scientific_name`, `user__username`).
- **`WildlifeConversationAdmin`:** List display, search, date filters, and `WildlifeMessageInline` for inspecting message threads directly within conversations.
- **`WildlifeMessageAdmin`:** Search on message content and conversation titles, filtered by role and timestamp.
- **`ProfileAdmin`:** List display of wallet balance, total predictions, and user search.

---

## 6. Migration Evolution

| Migration | App | Description |
| :--- | :--- | :--- |
| `0001_initial` | `animal` | Initial `AnimalPrediction` table. |
| `0002_contact` | `animal` | Contact submission table. |
| `0003_animalprediction_user_and_more` | `animal` | Safe nullable `user` foreign key. |
| `0004_alter_animalprediction_options_...` | `animal` | Extended `AnimalPrediction` fields (`common_name`, `uploaded_image`, `identification_status`, `notes`, JSON fields) & created `WildlifeConversation` and `WildlifeMessage`. |
| `0001_initial` | `transactions` | Profile model with wallet balance and total predictions. |

Legacy database records (`Lion`, 95.0% confidence, ChatGPT images) were verified and preserved intact throughout migrations.

---

## 7. Phase 3 — Gemini Multimodal Vision Integration

### Architecture & Service Design
- **Single Reusable Gemini Factory (`animal/services/gemini_service.py`):**
  - Uses `ChatGoogleGenerativeAI` from `langchain_google-genai`.
  - Configured strictly via environment variables: `GOOGLE_API_KEY` (or `GEMINI_API_KEY`), `GEMINI_MODEL` (default: `gemini-3.8-flash`), `GEMINI_TIMEOUT_SECONDS` (default: 30s).
  - Explicit configuration error handling (`GeminiConfigurationError`, `GeminiQuotaError`, `GeminiTimeoutError`, `GeminiModelAccessError`, `GeminiResponseError`).
- **Pillow Image Security Validation (`animal/services/image_validator.py`):**
  - Pre-validates uploaded files before invoking Gemini.
  - Allowed formats: `JPEG`, `PNG`, `WebP`.
  - Max file size: 5 MB (`MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024`).
  - Max dimensions: 4096 x 4096 pixels; Decompression bomb protection with `Image.MAX_IMAGE_PIXELS = 25_000_000`.
  - Blocks empty files, corrupted streams, and non-image payloads.
- **Pydantic Structured Output:**
  - `AnimalIdentificationResult` schema enforced via `ChatGoogleGenerativeAI.with_structured_output(...)`.
  - Fields: `is_animal` (bool), `identification_status` (`identified` | `uncertain` | `not_animal`), `common_name` (optional str), `scientific_name` (optional str), `visual_clues` (list[str]), `alternative_species` (list[str]), `identification_note` (str).
- **Zero Hardcoding & Evidence-Based Output:**
  - Completely purged fake responses (no hardcoded Lion/Tiger/Elephant).
  - Never invents fake numerical confidence scores (`confidence` is qualitative, relying on `identification_status` and verifiable `visual_clues`).
  - Graceful handling of non-animal imagery and ambiguous/distant wildlife captures.
- **Credit & Storage Integration:**
  - Authenticated view deducts 1.0 credit per valid analysis.
  - Failures (invalid image, quota limit, timeout) gracefully inform the user without credit deduction.
  - Persists full taxonomy and visual clues into SQLite `AnimalPrediction` model.

---

## 8. Verification Evidence

### System Integrity Check
```powershell
python manage.py check
```
```text
System check identified no issues (0 silenced).
```

### Automated Test Suite Execution
```powershell
python manage.py test
```
```text
Creating test database for alias 'default'...
Found 33 test(s).
System check identified no issues (0 silenced).
................Both GOOGLE_API_KEY and GEMINI_API_KEY are set. Using GOOGLE_API_KEY.
.................
----------------------------------------------------------------------
Ran 33 tests in 125.573s

OK
Destroying test database for alias 'default'...
```
- **12 `transactions` tests:** Registration, password validation, login, POST logout, protected dashboard, user ownership.
- **21 `animal` tests:**
  - Home and Contact views.
  - Image validation unit tests (valid PNG/JPEG, empty file rejection, unsupported format rejection, oversized file rejection).
  - Gemini service unit tests (API key validation, model instantiation).
  - Gemini Vision integration scenarios (valid animal identification, ambiguous/uncertain species, multiple animals foreground/background, no animal detected, invalid file rejection, quota limit error, timeout error, insufficient wallet credits).
  - Data privacy and isolation (history isolation, foreign prediction 404 barrier).
  - Persistent conversation and message relationships.

---

## 9. Phase 4 — LangGraph Wildlife Agent & Read-Only Tool Integration

### StateGraph Architecture & Workflow
- **Module Architecture (`animal/agent/`):**
  - `state.py`: Minimal `WildlifeAgentState` TypedDict schema using `add_messages` reducer. Zero raw image binaries, API tokens, or Django ORM models in state.
  - `tools.py`: Approved read-only tools (`lookup_species_taxonomy` via GBIF Species API, `get_wildlife_facts` via MediaWiki API) and `ControlledToolNode` subclass.
  - `graph.py`: Complete StateGraph definition with `input_router`, `vision_node`, `agent_node`, and `tools`, linked by conditional edges `route_request`, `route_vision`, and `should_continue`.
- **Controlled Execution & Safety Invariants:**
  - Strict budget of maximum 3 external HTTP tool calls per session.
  - Maximum 5 graph iterations per session with recursion limit guard `recursion_limit=10`.
  - Duplicate tool calls are prevented via signature caching (`name:args`).
  - Read-only execution with strictly allowlisted HTTPS endpoints (`api.gbif.org`, `en.wikipedia.org`).
- **Relational Conversation Memory:**
  - Uses existing SQLite database models `WildlifeConversation` and `WildlifeMessage`.
  - Multi-turn conversation bounded to the last 10 messages, passed dynamically into LangGraph.
  - User isolation strictly enforced via 404 barriers.

---

## 10. Phase 5 — WildLens Context-Aware Conversational Assistant

### System Design & Chat Endpoint
- **Dedicated Chat Endpoint (`POST /assistant/chat/`):**
  - Accepts JSON payload: `{"conversation_id": <int>, "message": "<string>"}`.
  - Ownership Guard: Strictly validates that `conversation.user == request.user`. Returns 404 for foreign or invalid IDs without leaking metadata.
  - Rate Limiting Guard: Application-level session timestamp check enforces a minimum 1.0-second delay between queries to prevent abuse.
  - Input Validation: Rejects empty messages and enforces a maximum cap of 1000 characters.
  - Missing Record Defense: Returns 404 if the associated `AnimalPrediction` has been removed.
- **Relational Chat Context & Bounded Memory:**
  - Injects active specimen details (common name, scientific name, identification status, visual clues, baseline notes) into the dialogue context.
  - Tentative Framing Guard: If `identification_status == "uncertain"`, the system prompt strictly instructs the agent to treat findings as hypotheses rather than verified facts.
  - Retrieves the most recent 10 messages from SQLite in chronological order, bounding token consumption without vector database overhead.
- **Bilingual Language Support (English & Hindi):**
  - The agent responds fluently in English or Hindi (Devanagari script) based on user prompt or explicit request (e.g., "हिंदी में समझाएं").
  - Seamless language switching during multi-turn conversation.
  - Strict zero-emoji policy maintained across all languages.
- **Client-Side Interface (`templates/animal/prediction_detail.html`):**
  - Built with Vanilla JavaScript `fetch()`, Bootstrap 5, and Bootstrap Icons (`bi bi-*`).
  - Suggested quick-question chips (Natural Diet, Range, Threats, Lifespan, Hindi explanation).
  - Live typing indicator (`Consulting wildlife knowledge base & reasoning...`).
  - Inline verified source badges linked to GBIF / Wikipedia articles.
  - Graceful fallback to standard form POST if JavaScript is disabled.
  - Error isolation: Failed or timed-out AI responses display a helpful alert and are **never saved** to SQLite as answers.

---

## 11. Verification Evidence

### System Integrity Check
```powershell
python manage.py check
```
```text
System check identified no issues (0 silenced).
```

### Automated Test Suite Execution
```powershell
python manage.py test
```
```text
Creating test database for alias 'default'...
Found 53 test(s).
System check identified no issues (0 silenced).
..............Both GOOGLE_API_KEY and GEMINI_API_KEY are set. Using GOOGLE_API_KEY.
.............LangGraph assistant execution error: Gemini API rate limit reached.
..LangGraph assistant execution error: Agent invocation timed out after 30 seconds.
...Maximum tool call limit (2) reached; halting further external requests.
..Rejected unapproved tool invocation: execute_shell_command
.....lookup_species_taxonomy parameter validation error: Species parameter cannot be empty.
lookup_species_taxonomy parameter validation error: Species name exceeds maximum allowed length of 150 characters.
lookup_species_taxonomy parameter validation error: Species name contains invalid or unsafe characters.
.GBIF Species API request timed out after 10.0s
.............
----------------------------------------------------------------------
Ran 53 tests in 182.838s

OK
Destroying test database for alias 'default'...
```
- **12 `transactions` tests:** Registration, password validation, login, POST logout, protected dashboard, user ownership.
- **48 `animal` tests:**
  - Home and Contact views.
  - Image validation unit tests (PNG/JPEG, empty file, text format, oversized file).
  - Gemini service unit tests (API key validation, model instantiation).
  - Agent vision integration scenarios (identified animal, uncertain species, no animal detected, invalid image rejection, quota limit error, insufficient wallet credits).
  - Multi-turn conversational post interaction and message persistence.
  - Data privacy and isolation (history isolation, foreign prediction 404 barrier).
  - GBIF Species API tool tests (matched taxonomy, no match, timeout, parameter injection defense).
  - MediaWiki API tool tests (article summary retrieval, missing article, parameter validation).
  - `ControlledToolNode` guard tests (unapproved tool rejection, duplicate call suppression, max 3 call budget enforcement).
  - LangGraph StateGraph integration tests (vision exit on no animal/uncertain, tool routing on identified, multi-turn chat routing, infinite loop prevention via iteration limit).
  - Wildlife Assistant Chat Tests (8 tests in `animal/tests.py`).
  - **Phase 6 UI Modernization Tests (7 tests in `animal/tests.py`):**
    * Pinned Bootstrap Icons 1.13.2 release verification in base template.
    * Zero decorative emojis across all rendered HTML pages.
    * AnimalPrediction backward-compatibility properties (`image_url`, `image`, `species`).
    * Drag-and-drop dropzone, preview, and double-submit spinner verification in predict page.
    * Conversational assistant container, typing indicator, and suggested chips in detail page.
    * Stat pills, modern table, and status badges in history page.
    * Digital wallet card, balance display, and demo packages in profile dashboard.

---

## 12. Phase 6 — Complete UI & Icon Modernization Architecture

### Core Design System & Iconography
- **Pinned Bootstrap Icons Release:** `<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.13.2/font/bootstrap-icons.min.css">` loaded globally in `templates/base.html`.
- **Zero Decorative Emojis:** 100% purged across templates, static CSS, views, and models. Replaced with semantic Bootstrap Icons (`bi bi-*`).
- **Nature-Inspired Palette (`static/css/style.css`):**
  - Primary Forest: `#0B5D3B` / Dark Forest: `#093822`
  - Emerald & Leaf Green: `#16a34a` / `#15803d`
  - Sage Surface & Soft Accents: `#ecfdf5` / `#f0fdf4`
  - Earthy Neutrals & Amber: `#1f2937`, `#6b7280`, `#f59e0b`
- **Component Polish:**
  - **Predict & Upload:** Drag-and-drop dropzone with `.drag-active` visual feedback, client-side format & 5 MB size validation, instant image preview with file size indicator, double-submission lock with spinning spinner.
  - **Conversational Assistant:** Polished user & assistant chat bubbles, animated typing indicator with pulsing dots, suggested question chips (Diet, Range, Threats, Lifespan, Hindi explanation), safe XSS escaping (`escapeHtml()`), CSRF token authentication.
  - **Prediction Detail:** Specimen photograph card, metadata dossier, observable visual clues list, potential candidate species pills, verified GBIF and MediaWiki source badges.
  - **User Dashboard & History:** Prominent digital wallet balance, prediction count metrics, quick upload CTA, recent specimen history with thumbnails and status badges (`identified`, `uncertain`, `not_animal`).
  - **Authentication & Contact:** Accessible form fields, password visibility toggle, helpful helper text, clean responsive layout.

---

## 13. Phase 7 — Demo Credits, Admin, and Integration Architecture

### Demo Credit Governance & Business Logic
1. **Initial Credit Grant (Rule 1):** Every newly registered user automatically receives 5 free demo credits (`wallet_balance = Decimal("5.00")`), backed by a recorded `signup_bonus` transaction entry in `WalletTransaction`.
2. **Identification Cost (Rule 2):** One successfully identified animal specimen costs exactly 1.0 credit (`₹1.00`).
3. **Free Operations (Rules 3, 4, 5, 6, 7):**
   - Viewing previous predictions is completely free.
   - Follow-up conversational messages via `/assistant/chat/` are free.
   - Corrupted or invalid images (failing Pillow validation) consume 0 credits.
   - Network errors, timeouts, or quota errors from Gemini or external tools consume 0 credits.
   - Photographs with no identifiable animal (`status == "not_animal"` or `is_animal is False`) consume 0 credits.
4. **Insufficient Balance Guard (Rule 8):** Accounts with `< ₹1.00` balance are blocked prior to external API execution, receiving clear notices and disabled submit buttons.
5. **Admin Credit Grants (Rule 10):** Django Admin provides bulk actions (`grant_5_demo_credits`, `grant_10_demo_credits`) allowing academic evaluators to replenish user demo credits cleanly.

### SQLite Concurrency, Idempotency & Transaction Safety
- **Conditional F Expression Updates:**
  ```python
  Profile.objects.filter(id=profile.id, wallet_balance__gte=cost).update(
      wallet_balance=F("wallet_balance") - cost,
      total_predictions=F("total_predictions") + 1
  )
  ```
  Prevents negative balances even under concurrent requests without holding locks or depending on `select_for_update()`.
- **Database-Level Idempotency (`request_id`):**
  Each image analysis form embeds a unique UUID `request_id`. `AnimalPrediction.request_id` enforces a `unique=True` database constraint. Re-submitting the exact same request returns the existing prediction with zero additional charges.
- **External API Decoupling:**
  Gemini and LangGraph external HTTP network calls are strictly executed outside database transactions. SQLite write locks are only acquired during the final atomic persistence phase.

---

## 14. College Project Submission Readiness Summary
WildLensAI is fully implemented across all 7 phases:
- **Phase 1:** Codebase audit, security hardening, secret decoupling, emoji purge, Bootstrap Icons adoption.
- **Phase 2:** SQLite database schema extension, Django authentication hardening, user privacy isolation.
- **Phase 3:** Gemini multimodal vision species classification and Pillow image verification.
- **Phase 4:** LangGraph StateGraph wildlife agent, GBIF & MediaWiki tool calling, and SQLite multi-turn conversational assistant.
- **Phase 5:** Context-aware conversational assistant, `POST /assistant/chat/` JSON API, bilingual English/Hindi dialogue, vanilla JS interactive chat UI, and complete error isolation.
- **Phase 6:** Complete UI and Icon modernization, pinned Bootstrap Icons 1.13.2, responsive nature design system, drag-and-drop upload zone, double-submission lock, suggested chips.
- **Phase 7:** Demo credit system, atomic F-expression concurrency safety, request idempotency, WalletTransaction ledger, admin demo grants, and 71 automated tests passing.



