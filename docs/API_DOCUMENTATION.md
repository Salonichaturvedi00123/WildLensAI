# WildLensAI — API & Endpoint Documentation

This document describes all internal HTTP endpoints, request/response contracts, validation schemas, error codes, and external read-only tool APIs used in **WildLensAI**.

---

## 1. Internal Application Endpoints

### A. Wildlife Identification & Prediction

#### `POST /predict/`
Analyzes an uploaded wildlife image using Gemini Multimodal Vision and the LangGraph Agent pipeline.

- **Authentication:** Required (`@login_required`)
- **Content-Type:** `multipart/form-data`
- **Request Parameters:**
  - `image` *(File, required)*: Uploaded image (`image/jpeg`, `image/png`, `image/webp`). Max size: 5.0 MB.
  - `request_id` *(String, optional)*: UUIDv4 generated on form render to enforce idempotency.
- **Cost:**
  - `₹1.00` demo credit if an animal is identified or uncertain.
  - `₹0.00` demo credits if `not_animal` or validation/API failure.
- **Response:**
  - **Success (HTTP 200):** Renders `animal/predict.html` with populated `prediction` context, visual clues, and credit status.
  - **Insufficient Credits (HTTP 200):** Displays warning banner (`insufficient_credits: True`) and disables submit.
  - **Validation Error (HTTP 200):** Displays error notification via Django messages.

---

#### `POST /assistant/chat/`
Interactive, context-aware conversational assistant grounded in the focal identified wildlife specimen.

- **Authentication:** Required (`@login_required`)
- **Content-Type:** `application/json` (also accepts `application/x-www-form-urlencoded`)
- **Request Body:**
  ```json
  {
    "conversation_id": 1,
    "message": "What does this tiger eat in the wild?"
  }
  ```
- **Language Support:** Automatically responds in Hindi (Devanagari script) if queried in Hindi or requested ("हिंदी में समझाएं").
- **Cost:** `₹0.00` (Follow-up conversations are 100% free of charge).
- **Response Contract (JSON):**
  - **Success (HTTP 200):**
    ```json
    {
      "success": true,
      "answer": "Tigers are apex obligate carnivores that primarily hunt ungulates such as chital, sambar, and wild boar.",
      "conversation_id": 1,
      "sources": [
        {
          "title": "Panthera tigris",
          "url": "https://www.gbif.org/species/5219404",
          "source": "GBIF"
        }
      ],
      "created_at": "07:15 AM"
    }
    ```
  - **Error (HTTP 400 / 403 / 500 / 503):**
    ```json
    {
      "success": false,
      "error": "Descriptive error or rate limit message"
    }
    ```

---

#### `GET /history/`
Displays the authenticated user's chronological prediction history.

- **Authentication:** Required (`@login_required`)
- **Privacy:** Users strictly see only their own prediction records (`user=request.user`).
- **Cost:** `₹0.00` (Free).

---

#### `GET /predictions/<prediction_id>/`
Renders the complete diagnostic dossier for a previously saved wildlife identification, including full conversation history, visual clues, alternative candidate species, and verified source links.

- **Authentication:** Required (`@login_required`)
- **Privacy:** Returns HTTP 404 if accessed by a user who does not own the prediction.
- **Cost:** `₹0.00` (Free).

---

### B. User Management & Demo Wallet

#### `POST /transactions/register/`
Registers a new user account, validates password strength with Django built-in validators, provisions a `Profile`, and awards **₹5.00 demo credits**.

- **Fields:** `first_name`, `last_name`, `username`, `email`, `password`, `confirm_password`.
- **Response:** Redirects to `/transactions/login/` on success.

#### `POST /transactions/login/`
Authenticates existing users via Django session authentication.

#### `POST /transactions/logout/`
Safely destroys the user session using a CSRF-protected POST request.

#### `GET /transactions/profile/` & `GET /transactions/dashboard/`
Displays user metadata, current wallet balance, and full **Wallet Transaction Ledger** (`signup_bonus`, `prediction_charge`, `admin_grant`, etc.).

---

## 2. External Read-Only Tool APIs

The LangGraph agent has access to two read-only, whitelisted HTTP tools executed via `httpx`:

### 1. GBIF Species API (`lookup_species_taxonomy`)
- **Base URL:** `https://api.gbif.org/v1/species/match`
- **Method:** `GET`
- **Query Parameter:** `name=<species_name>`
- **Timeout:** 10.0 seconds
- **Returned Data:**
  - `accepted_scientific_name`
  - `kingdom`, `phylum`, `class`, `order`, `family`, `genus`, `species`
  - `match_type` (`EXACT`, `FUZZY`, `HIGHERRANK`)
  - `source_url`: `https://www.gbif.org/species/<usageKey>`

### 2. MediaWiki API (`get_wildlife_facts`)
- **Base URL:** `https://en.wikipedia.org/w/api.php`
- **Method:** `GET`
- **Query Parameters:** `action=query&format=json&prop=extracts&exintro=1&explaintext=1&titles=<species_name>`
- **Timeout:** 10.0 seconds
- **Returned Data:**
  - `article_title`
  - `summary` (educational narrative extract)
  - `source_url`: `https://en.wikipedia.org/wiki/<article_title>`
  - `conservation_status`: "Not verified" (prevents unverified IUCN assertions)

