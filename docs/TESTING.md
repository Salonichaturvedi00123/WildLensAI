# WildLensAI — Comprehensive Testing Guide & Verification Report

This document details the automated test suite, mocking strategies, image identification test categories, LangGraph verification checks, concurrency tests, and manual testing procedures for **WildLensAI**.

---

## 1. Test Suite Architecture

The automated test suite contains **81 automated test cases** implemented using Django's built-in `TestCase` and `unittest.mock`.

```text
Test Suite Summary:
├── transactions/tests.py (19 Tests)
│   ├── TransactionsAuthTests (12 tests)
│   │   ├── Registration, Password Validation, Mismatches
│   │   ├── Login, Logout via POST, Dashboard protection
│   │   └── Profile creation & 5 initial demo credits
│   └── Phase7WalletDemoCreditTests (7 tests)
│       ├── Rule 1: 5 signup credits & ledger entry
│       ├── Rule 2: 1 credit deduction for valid identification
│       ├── Rule 8: Insufficient balance blocks prediction
│       ├── Rule D: Idempotency prevents double deduction
│       ├── Concurrency: Conditional F expression avoids negative balance
│       ├── Rule 10: Admin demo credit grants
│       └── User isolation across transaction ledgers
│
└── animal/tests.py (62 Tests)
    ├── AnimalAppTests (5 tests: Views, contact, auth checks)
    ├── WildlifeToolsUnitTests (9 tests: GBIF, MediaWiki, tool limits)
    ├── LangGraphStateGraphIntegrationTests (5 tests: Routing, loops)
    ├── ImageValidatorUnitTests (4 tests: Pillow safety, format, size)
    ├── GeminiServiceUnitTests (2 tests: Factory, API keys)
    ├── WildlifeAssistantChatTests (8 tests: Chat, Hindi, context, errors)
    ├── UIModernizationTests (7 tests: Bootstrap Icons, zero emojis)
    ├── Phase7DemoCreditsIntegrationTests (4 tests: Free views, chat, idempotent)
    └── Phase8ImageIdentificationAndVivaTests (10 tests: 9 image categories + Hindi)
```

---

## 2. Test Execution Commands & Results

### Running All Automated Tests
```powershell
python manage.py test
```

### Output:
```powershell
Creating test database for alias 'default'...
Found 81 test(s).
System check identified no issues (0 silenced).
.................................................................................
----------------------------------------------------------------------
Ran 81 tests in 286.115s

OK
Destroying test database for alias 'default'...
```

---

## 3. Image Identification Test Categories (Phase 8 Specification D)

| Category | Specimen Tested | Diagnostic Traits Observed | Expected Status | Credits Deducted |
| :--- | :--- | :--- | :--- | :--- |
| **1. Tiger** | *Panthera tigris tigris* | Reddish-orange coat, black vertical stripes, white belly | `identified` | ₹1.00 |
| **2. Lion** | *Panthera leo* | Tawny coat, dark dorsal neck mane, tufted tail | `identified` | ₹1.00 |
| **3. Elephant** | *Elephas maximus* | Twin-domed head, small ears, prehensile trunk | `identified` | ₹1.00 |
| **4. Bird** | *Pavo cristatus* | Iridescent blue plumage, fan crest, ocellated train | `identified` | ₹1.00 |
| **5. Multiple Animals** | *Axis axis* & *Sus scrofa* | Mixed ungulates (spotted deer and wild boars) | `identified` | ₹1.00 |
| **6. Unclear Animal** | *Uncertain Canid / Mustelid* | Low silhouette, motion blur, ambiguous cranial marks | `uncertain` | ₹1.00 |
| **7. No Animal** | Ceramic coffee mug / landscape | Inanimate object, no fauna visible | `not_animal` | **₹0.00 (FREE)** |
| **8. Unsupported Image**| Text file (`assignment.txt`) | Rejected by Pillow before Gemini API invocation | N/A (Error) | **₹0.00 (FREE)** |
| **9. Corrupted Image** | Truncated PNG byte stream | Rejected by `Image.verify()` decompression check | N/A (Error) | **₹0.00 (FREE)** |

---

## 4. LangGraph Verification Checklist (12 Points)

1. [x] **StateGraph Compiles Correctly:** `build_wildlife_agent_graph()` builds and compiles the cyclic graph with START, END, and registered nodes.
2. [x] **Vision Node Works:** Multimodal analysis correctly populates `identification` in state.
3. [x] **Conditional Routing Works:** Routes between `vision_node`, `agent_node`, `tools`, and `END` based on request type and identification status.
4. [x] **ToolNode Executes Approved Tools:** Subclassed `ControlledToolNode` executes GBIF and MediaWiki APIs.
5. [x] **Model Selects Tools Dynamically:** Uses `model.bind_tools(APPROVED_TOOLS)` allowing dynamic tool selection.
6. [x] **Tool Results Return to Agent:** Cyclic edge `tools -> agent_node` returns `ToolMessage` payloads to the model.
7. [x] **Agent Can Answer Without Tools:** When information is already known, the agent produces direct answers without tool overhead.
8. [x] **Tool Execution Limits Respected:** Strict cap of maximum 3 external tool calls per session enforced.
9. [x] **No Infinite Loop Possible:** Graph iteration limit (`MAX_GRAPH_ITERATIONS = 5`) and recursion limit 10 prevent infinite looping.
10. [x] **Errors Terminate Safely:** Exceptions like timeouts or invalid formats return graceful messages without server crashes.
11. [x] **Chat Uses Same Agent Architecture:** `run_wildlife_agent` services both `request_type="image"` and `request_type="chat"`.
12. [x] **SQLite Conversation Memory Preserved:** Recent 10 messages loaded from `WildlifeMessage` provide persistent context.

---

## 5. Live Gemini Smoke Test Note

When testing external connectivity with a live Google API key:
- Automated test suites strictly utilize **mocked responses** to guarantee fast, deterministic, and offline execution without exhausting API quotas.
- Outbound network requests to Google Gemini endpoints are governed by a **30-second timeout**. If network transit is interrupted or rate limits are reached, `GeminiTimeoutError` or `GeminiQuotaError` is cleanly caught, returning an informative message to the user without leaving SQLite transactions open or corrupting data.

---

## 6. Manual Testing Checklist for Viva Demonstration

1. **User Sign Up:**
   - Navigate to `/transactions/register/`.
   - Create user `viva_student`.
   - Check dashboard `/transactions/profile/`: confirm initial balance of **₹5.00** and a `signup_bonus` transaction entry.
2. **Predict Animal (Bengal Tiger):**
   - Upload `media/uploads/tiger.png`.
   - Verify identification: *Panthera tigris*, visual clues, and GBIF source link.
   - Confirm balance is now **₹4.00** (₹1.00 deducted).
3. **Contextual Chat (English & Hindi):**
   - In the chat box on the prediction detail page, ask: *"What does this tiger eat?"*
   - Verify contextual response referencing ungulates.
   - Ask in Hindi: *"यह कहाँ पाया जाता है?"*
   - Verify fluent Hindi response in Devanagari script.
   - Confirm wallet balance is still **₹4.00** (chat is free).
4. **Non-Animal Upload (Free):**
   - Upload `media/uploads/mug.png`.
   - Verify message: *"No wildlife detected in the uploaded photograph. Zero credits deducted."*
   - Confirm wallet balance remains **₹4.00** (0 credits consumed).
5. **Admin Credit Grant:**
   - Log in to `/admin/` as superuser.
   - Select profile `viva_student` and choose action *"Grant +5 Demo Credits"*.
   - Return to user profile: balance is now **₹9.00** with an `admin_grant` transaction logged.

