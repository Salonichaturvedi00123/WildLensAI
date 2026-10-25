/**
 * WildLensAI - Razorpay Standard Checkout & Wallet Recharge Integration
 * Handles server-side order creation, official checkout.js modal,
 * cryptographic signature verification, error recovery, and order reconciliation.
 */

(function () {
    "use strict";

    let isCheckoutInFlight = false;

    function devLog(msg, data) {
        if (typeof console !== "undefined" && console.log) {
            if (data !== undefined) {
                console.log("[WildLens Checkout] " + msg, data);
            } else {
                console.log("[WildLens Checkout] " + msg);
            }
        }
    }

    function devWarn(msg, data) {
        if (typeof console !== "undefined" && console.warn) {
            if (data !== undefined) {
                console.warn("[WildLens Checkout] " + msg, data);
            } else {
                console.warn("[WildLens Checkout] " + msg);
            }
        }
    }

    function devError(msg, err) {
        if (typeof console !== "undefined" && console.error) {
            if (err !== undefined) {
                console.error("[WildLens Checkout Error] " + msg, err);
            } else {
                console.error("[WildLens Checkout Error] " + msg);
            }
        }
    }

    function getCsrfToken() {
        const input = document.querySelector('input[name="csrfmiddlewaretoken"]');
        if (input && input.value) {
            return input.value;
        }
        const match = document.cookie.match(/csrftoken=([^;]+)/);
        return match ? match[1] : "";
    }

    function getOrCreateAlertContainer() {
        let container = document.getElementById("checkout-status-alert-container");
        if (!container) {
            container = document.createElement("div");
            container.id = "checkout-status-alert-container";
            container.className = "mb-4";
            const target = document.querySelector("main") || document.querySelector(".container") || document.body;
            target.insertBefore(container, target.firstChild);
        }
        return container;
    }

    function showStatusBanner(type, title, message, actionHtml = "") {
        const container = getOrCreateAlertContainer();
        if (!container) return;

        let alertClass = "alert-info";
        let iconClass = "bi-info-circle-fill text-info";

        if (type === "success") {
            alertClass = "alert-success";
            iconClass = "bi-check-circle-fill text-success";
        } else if (type === "error" || type === "failed") {
            alertClass = "alert-danger";
            iconClass = "bi-exclamation-triangle-fill text-danger";
        } else if (type === "warning" || type === "pending") {
            alertClass = "alert-warning";
            iconClass = "bi-hourglass-split text-warning";
        } else if (type === "loading") {
            alertClass = "alert-primary";
            iconClass = "spinner-border spinner-border-sm text-primary";
        }

        const isSpinner = type === "loading";
        const iconHtml = isSpinner
            ? `<div class="${iconClass} me-3 mt-1" role="status" aria-hidden="true"></div>`
            : `<i class="bi ${iconClass} fs-4 me-3 mt-1" aria-hidden="true"></i>`;

        container.innerHTML = `
            <div class="alert ${alertClass} alert-dismissible fade show shadow-sm d-flex align-items-start p-3 mb-4 rounded-3" role="alert">
                ${iconHtml}
                <div class="flex-grow-1">
                    <h6 class="alert-heading fw-bold mb-1">${title}</h6>
                    <div class="small mb-1">${message}</div>
                    ${actionHtml ? `<div class="mt-2">${actionHtml}</div>` : ""}
                </div>
                <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
            </div>
        `;
        container.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }

    function updateWalletBalanceUI(newBalance) {
        if (typeof newBalance === "undefined" || newBalance === null) return;
        const formatted = "₹" + Number(newBalance).toFixed(2);

        // Update all balance placeholders across navbar and recharge cards
        document.querySelectorAll(".wallet-balance-display").forEach(function (el) {
            el.textContent = formatted;
        });
        document.querySelectorAll(".wallet-balance-amount").forEach(function (el) {
            el.textContent = Number(newBalance).toFixed(2);
        });
    }

    function setAllPlanButtonsDisabled(disabled) {
        document.querySelectorAll(".btn-recharge-plan").forEach(function (btn) {
            btn.disabled = disabled;
        });
    }

    function resetPlanButton(buttonEl, originalHtml) {
        isCheckoutInFlight = false;
        setAllPlanButtonsDisabled(false);
        if (buttonEl && originalHtml) {
            buttonEl.innerHTML = originalHtml;
        }
    }

    function loadRazorpaySdk() {
        return new Promise(function (resolve) {
            if (typeof window.Razorpay !== "undefined") {
                resolve(true);
                return;
            }
            const existingScript = document.querySelector('script[src*="checkout.razorpay.com"]');
            if (existingScript) {
                existingScript.addEventListener("load", function () { resolve(true); });
                existingScript.addEventListener("error", function () { resolve(false); });
                if (typeof window.Razorpay !== "undefined") {
                    resolve(true);
                }
                return;
            }
            const script = document.createElement("script");
            script.src = "https://checkout.razorpay.com/v1/checkout.js";
            script.onload = function () { resolve(true); };
            script.onerror = function () { resolve(false); };
            document.head.appendChild(script);
        });
    }

    async function initiateRecharge(planId, buttonEl) {
        if (!planId) {
            devWarn("initiateRecharge called without planId");
            return;
        }

        if (isCheckoutInFlight) {
            devWarn("Checkout initiation already in flight. Ignoring repeated click.");
            return;
        }
        isCheckoutInFlight = true;

        // Visual loading state and lock buttons against duplicate submission
        const originalHtml = buttonEl ? buttonEl.innerHTML : "";
        setAllPlanButtonsDisabled(true);
        if (buttonEl) {
            buttonEl.innerHTML = `<span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span> Preparing secure checkout...`;
        }

        // Required message: "Preparing secure checkout..."
        showStatusBanner(
            "loading",
            "Preparing Secure Checkout",
            "Preparing secure checkout..."
        );

        devLog("Creating order for plan:", planId);

        try {
            const createUrl = buttonEl && buttonEl.dataset.createUrl
                ? buttonEl.dataset.createUrl
                : "/transactions/orders/create/";

            const response = await fetch(createUrl, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRFToken": getCsrfToken(),
                },
                body: JSON.stringify({ plan_id: planId }),
            });

            const data = await response.json();
            devLog("Order creation response status:", response.status);

            if (!response.ok || !data.success) {
                devError("Order creation rejected:", data);
                resetPlanButton(buttonEl, originalHtml);
                const errorMsg = data.error || "We couldn't complete your payment. Please try again.";
                showStatusBanner(
                    "error",
                    "Order Creation Error",
                    "We couldn't complete your payment. Please try again." +
                    `<br><small class="text-muted mt-1 d-inline-block">${errorMsg}</small>`,
                    `<button class="btn btn-sm btn-outline-danger mt-1" onclick="window.location.reload()">
                        <i class="bi bi-arrow-clockwise me-1"></i> Try Again
                    </button>`
                );
                return;
            }

            devLog("Order created successfully:", data.order ? data.order.razorpay_order_id : null);

            // Ensure Razorpay SDK is available
            const sdkLoaded = await loadRazorpaySdk();
            if (!sdkLoaded || typeof window.Razorpay === "undefined") {
                devError("Failed to load official Razorpay SDK checkout.js");
                resetPlanButton(buttonEl, originalHtml);
                showStatusBanner(
                    "error",
                    "Checkout Gateway Unavailable",
                    "We couldn't complete your payment. Please try again. Razorpay checkout script could not be loaded."
                );
                return;
            }

            // Launch official Razorpay Standard Checkout modal
            openRazorpayCheckout(data.order, buttonEl, originalHtml);

        } catch (err) {
            devError("Order creation network error:", err);
            resetPlanButton(buttonEl, originalHtml);
            showStatusBanner(
                "error",
                "Communication Error",
                "We couldn't complete your payment. Please try again."
            );
        }
    }

    function openRazorpayCheckout(orderData, buttonEl, originalHtml) {
        if (typeof window.Razorpay === "undefined") {
            resetPlanButton(buttonEl, originalHtml);
            showStatusBanner(
                "error",
                "Checkout Unavailable",
                "We couldn't complete your payment. Please try again."
            );
            return;
        }

        // Required message: "Complete your payment securely."
        showStatusBanner(
            "info",
            "Payment Gateway Ready",
            "Complete your payment securely."
        );

        devLog("Launching Razorpay modal for order:", orderData.razorpay_order_id);

        const options = {
            key: orderData.key_id,
            amount: orderData.amount,
            currency: orderData.currency || "INR",
            name: "WildLens AI",
            description: `${orderData.plan_name} (+${orderData.credits} Credits)`,
            image: "https://raw.githubusercontent.com/twbs/icons/main/icons/shield-check.svg",
            order_id: orderData.razorpay_order_id,
            prefill: {
                name: (orderData.prefill && orderData.prefill.name) || "",
                email: (orderData.prefill && orderData.prefill.email) || "",
                contact: (orderData.prefill && orderData.prefill.contact) || "",
            },
            notes: {
                purpose: "WildLens AI Wildlife Discovery Credits Recharge",
                plan_name: orderData.plan_name,
            },
            theme: {
                color: "#1e3d2f", // WildLens Forest
            },
            modal: {
                backdropclose: false,
                escape: false,
                ondismiss: function () {
                    devLog("Razorpay modal dismissed by user.");
                    resetPlanButton(buttonEl, originalHtml);
                    // Required message: "Payment was cancelled. No credits were added."
                    showStatusBanner(
                        "warning",
                        "Payment Cancelled",
                        "Payment was cancelled. No credits were added.",
                        `<button class="btn btn-sm btn-outline-warning mt-1" onclick="window.WildLensWallet.reconcileOrder('${orderData.razorpay_order_id}')">
                            <i class="bi bi-arrow-repeat me-1"></i> Check Status &amp; Reconcile
                        </button>`
                    );
                },
            },
            handler: function (response) {
                devLog("Razorpay checkout success callback received:", response.razorpay_payment_id);
                verifyPayment(response, buttonEl, originalHtml);
            },
        };

        const rzp = new window.Razorpay(options);

        rzp.on("payment.failed", function (resp) {
            devWarn("Razorpay payment.failed callback:", resp.error);
            resetPlanButton(buttonEl, originalHtml);
            // Required message: "We couldn't complete your payment. Please try again."
            const desc = resp.error && resp.error.description ? resp.error.description : "";
            showStatusBanner(
                "error",
                "Payment Failed",
                "We couldn't complete your payment. Please try again." +
                (desc ? `<br><small class="text-muted mt-1 d-inline-block">${desc}</small>` : "")
            );
        });

        // Open Razorpay Standard Checkout
        rzp.open();
    }

    async function verifyPayment(rzpResponse, buttonEl, originalHtml) {
        devLog("Initiating server-side payment verification...");

        // Required message: "Verifying your payment..."
        showStatusBanner(
            "loading",
            "Payment Verification",
            "Verifying your payment..."
        );

        try {
            const verifyUrl = "/transactions/orders/verify/";
            const response = await fetch(verifyUrl, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRFToken": getCsrfToken(),
                },
                body: JSON.stringify({
                    razorpay_order_id: rzpResponse.razorpay_order_id,
                    razorpay_payment_id: rzpResponse.razorpay_payment_id,
                    razorpay_signature: rzpResponse.razorpay_signature,
                }),
            });

            const data = await response.json();
            devLog("Verification response received:", data);

            resetPlanButton(buttonEl, originalHtml);

            if (response.ok && data.success && data.status === "paid") {
                // Update live balance displays across navbar and cards
                updateWalletBalanceUI(data.wallet_balance);

                // Required message: "Credits added successfully. You're ready for your next wildlife discovery."
                showStatusBanner(
                    "success",
                    "Payment Complete",
                    "Credits added successfully. You're ready for your next wildlife discovery." +
                    `<br><strong class="text-success mt-1 d-inline-block">+${data.credits_granted} Credits Added. New Balance: ₹${Number(data.wallet_balance).toFixed(2)}</strong>`,
                    `<a href="/predict/" class="btn btn-sm btn-success me-2 mt-2">
                        <i class="bi bi-camera me-1"></i> Start Discovering Wildlife
                    </a>
                    <button class="btn btn-sm btn-outline-secondary mt-2" onclick="window.location.reload()">
                        <i class="bi bi-arrow-clockwise me-1"></i> Refresh History
                    </button>`
                );

                // Auto-refresh page after 3.5 seconds to refresh the database ledger & audit table
                setTimeout(function () {
                    window.location.reload();
                }, 3500);

            } else if (data.status === "pending") {
                // Required message: "Your payment is being verified. Please check your transaction history."
                showStatusBanner(
                    "warning",
                    "Payment Under Verification",
                    "Your payment is being verified. Please check your transaction history.",
                    `<button class="btn btn-sm btn-outline-warning mt-2" onclick="window.WildLensWallet.reconcileOrder('${rzpResponse.razorpay_order_id}')">
                        <i class="bi bi-arrow-repeat me-1"></i> Check Status Now
                    </button>`
                );
            } else {
                devError("Verification rejected by backend:", data);
                // Required message: "We couldn't complete your payment. Please try again."
                const errMsg = data.error || data.message || "";
                showStatusBanner(
                    "error",
                    "Verification Problem",
                    "We couldn't complete your payment. Please try again." +
                    (errMsg ? `<br><small class="text-muted mt-1 d-inline-block">${errMsg}</small>` : ""),
                    `<button class="btn btn-sm btn-outline-danger mt-2" onclick="window.WildLensWallet.reconcileOrder('${rzpResponse.razorpay_order_id}')">
                        <i class="bi bi-arrow-repeat me-1"></i> Check Status with Gateway
                    </button>`
                );
            }

        } catch (err) {
            devError("Verification network error:", err);
            resetPlanButton(buttonEl, originalHtml);

            // Network interrupted: Do NOT mark as permanently failed
            // Required message: "Your payment is being verified. Please check your transaction history."
            showStatusBanner(
                "warning",
                "Verification In Progress",
                "Your payment is being verified. Please check your transaction history.",
                `<button class="btn btn-sm btn-warning fw-semibold mt-2" onclick="window.WildLensWallet.reconcileOrder('${rzpResponse.razorpay_order_id}')">
                    <i class="bi bi-arrow-repeat me-1"></i> Reconcile Now
                </button>`
            );
        }
    }

    async function reconcileOrder(orderId, btnEl) {
        if (!orderId) return;

        const originalText = btnEl ? btnEl.innerHTML : "";
        if (btnEl) {
            btnEl.disabled = true;
            btnEl.innerHTML = `<span class="spinner-border spinner-border-sm me-1" role="status"></span> Checking...`;
        }

        // Required message: "Verifying your payment..."
        showStatusBanner(
            "loading",
            "Checking Gateway Status",
            "Verifying your payment..."
        );

        devLog("Reconciling order:", orderId);

        try {
            const url = `/transactions/orders/${encodeURIComponent(orderId)}/reconcile/`;
            const response = await fetch(url, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRFToken": getCsrfToken(),
                },
            });

            const data = await response.json();
            devLog("Reconciliation response:", data);

            if (btnEl) {
                btnEl.disabled = false;
                btnEl.innerHTML = originalText;
            }

            if (response.ok && data.success && data.status === "paid") {
                updateWalletBalanceUI(data.wallet_balance);
                // Required message: "Credits added successfully. You're ready for your next wildlife discovery."
                showStatusBanner(
                    "success",
                    "Payment Confirmed",
                    "Credits added successfully. You're ready for your next wildlife discovery." +
                    `<br><strong class="text-success mt-1 d-inline-block">Balance: ₹${Number(data.wallet_balance).toFixed(2)}</strong>`,
                    `<button class="btn btn-sm btn-success mt-2" onclick="window.location.reload()">
                        <i class="bi bi-arrow-clockwise me-1"></i> Refresh History
                    </button>`
                );
                setTimeout(function () {
                    window.location.reload();
                }, 2500);
            } else if (data.status === "pending") {
                // Required message: "Your payment is being verified. Please check your transaction history."
                showStatusBanner(
                    "warning",
                    "Payment Under Verification",
                    "Your payment is being verified. Please check your transaction history."
                );
            } else {
                // Required message: "We couldn't complete your payment. Please try again."
                const msg = data.message || data.error || "";
                showStatusBanner(
                    "error",
                    "Status Update",
                    "We couldn't complete your payment. Please try again." +
                    (msg ? `<br><small class="text-muted mt-1 d-inline-block">${msg}</small>` : "")
                );
            }

        } catch (err) {
            devError("Reconcile network error:", err);
            if (btnEl) {
                btnEl.disabled = false;
                btnEl.innerHTML = originalText;
            }
            showStatusBanner(
                "warning",
                "Verification Delay",
                "Your payment is being verified. Please check your transaction history."
            );
        }
    }

    function attachEventListeners() {
        // Recharge plan buttons
        document.querySelectorAll(".btn-recharge-plan").forEach(function (btn) {
            if (btn.dataset.listenerAttached === "true") return;
            btn.dataset.listenerAttached = "true";
            btn.addEventListener("click", function (e) {
                e.preventDefault();
                const planId = this.dataset.planId;
                devLog("Recharge button clicked for plan:", planId);
                initiateRecharge(planId, this);
            });
        });

        // Reconcile buttons in order tables
        document.querySelectorAll(".btn-reconcile-order").forEach(function (btn) {
            if (btn.dataset.listenerAttached === "true") return;
            btn.dataset.listenerAttached = "true";
            btn.addEventListener("click", function (e) {
                e.preventDefault();
                const orderId = this.dataset.orderId;
                devLog("Reconcile button clicked for order:", orderId);
                reconcileOrder(orderId, this);
            });
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", attachEventListeners);
    } else {
        attachEventListeners();
    }

    // Expose public API for inline button invocations or console debugging
    window.WildLensWallet = {
        initiateRecharge: initiateRecharge,
        reconcileOrder: reconcileOrder,
        verifyPayment: verifyPayment,
    };
})();
