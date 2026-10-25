import json
from decimal import Decimal
from django.utils import timezone
from django.db.models import Count, Sum
from django.db.models.functions import TruncDate
from django.contrib.auth.models import User
from animal.models import AnimalPrediction, WildlifeConversation, WildlifeMessage
from transactions.models import PaymentOrder, WalletTransaction, CreditPlan, Profile


def dashboard_callback(request, context):
    """
    Computes real SQLite database metrics, KPIs, and Chart.js datasets
    for the Django Unfold modern admin dashboard.
    """
    period = request.GET.get("period", "30d").lower().strip()
    if period not in ("7d", "30d", "all"):
        period = "30d"

    now = timezone.now()
    if period == "7d":
        start_date = now - timezone.timedelta(days=7)
        period_days = 7
        period_label = "Last 7 Days"
    elif period == "30d":
        start_date = now - timezone.timedelta(days=30)
        period_days = 30
        period_label = "Last 30 Days"
    else:
        start_date = None
        period_days = 30
        period_label = "All Time"

    # 1. User KPIs
    total_users_count = User.objects.count()
    new_users_period_qs = (
        User.objects.filter(date_joined__gte=start_date)
        if start_date
        else User.objects.all()
    )
    new_users_period_count = new_users_period_qs.count()

    # 2. Wildlife Prediction KPIs
    total_predictions_count = AnimalPrediction.objects.count()
    predictions_period_qs = (
        AnimalPrediction.objects.filter(created_at__gte=start_date)
        if start_date
        else AnimalPrediction.objects.all()
    )
    predictions_period_count = predictions_period_qs.count()

    identified_count = predictions_period_qs.filter(
        identification_status="identified"
    ).count()
    uncertain_count = predictions_period_qs.filter(
        identification_status="uncertain"
    ).count()
    not_animal_count = predictions_period_qs.filter(
        identification_status="not_animal"
    ).count()

    identified_rate = (
        round((identified_count / predictions_period_count * 100), 1)
        if predictions_period_count > 0
        else 0
    )

    # 3. AI Wildlife Conversations
    total_conversations_count = WildlifeConversation.objects.count()
    conversations_period_qs = (
        WildlifeConversation.objects.filter(created_at__gte=start_date)
        if start_date
        else WildlifeConversation.objects.all()
    )
    conversations_period_count = conversations_period_qs.count()
    total_messages_count = WildlifeMessage.objects.count()

    # 4. Payment Orders & Test Sandbox Volume
    paid_orders_qs = PaymentOrder.objects.filter(status="paid")
    paid_orders_period_qs = (
        paid_orders_qs.filter(created_at__gte=start_date)
        if start_date
        else paid_orders_qs
    )

    successful_captured_count = paid_orders_period_qs.count()
    pending_orders_count = PaymentOrder.objects.filter(
        status__in=["pending", "created"]
    ).count()

    credits_purchased_sum = (
        paid_orders_period_qs.aggregate(total=Sum("credits_to_grant"))["total"] or 0
    )
    test_volume_paise = (
        paid_orders_period_qs.aggregate(total=Sum("amount_paise"))["total"] or 0
    )
    test_volume_rupees = Decimal(test_volume_paise) / Decimal("100.00")

    # 5. Daily Aggregation Charts (Chart.js via Unfold canvas)
    chart_days = 7 if period == "7d" else 30
    chart_start_date = (now - timezone.timedelta(days=chart_days - 1)).date()
    date_list = [chart_start_date + timezone.timedelta(days=i) for i in range(chart_days)]
    date_labels = [d.strftime("%b %d") for d in date_list]
    date_keys = [d.strftime("%Y-%m-%d") for d in date_list]

    # Chart 1: User Registrations
    reg_rows = (
        User.objects.filter(date_joined__date__gte=chart_start_date)
        .annotate(day=TruncDate("date_joined"))
        .values("day")
        .annotate(total=Count("id"))
    )
    reg_map = {
        row["day"].strftime("%Y-%m-%d"): row["total"]
        for row in reg_rows
        if row["day"]
    }
    reg_data = [reg_map.get(k, 0) for k in date_keys]

    user_registrations_chart = json.dumps({
        "labels": date_labels,
        "datasets": [{
            "label": "New User Registrations",
            "data": reg_data,
            "borderColor": "#10b981",
            "backgroundColor": "rgba(16, 185, 129, 0.15)",
            "fill": True,
            "tension": 0.35,
        }]
    })

    # Chart 2: Animal Identifications
    pred_rows = (
        AnimalPrediction.objects.filter(created_at__date__gte=chart_start_date)
        .annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(total=Count("id"))
    )
    pred_map = {
        row["day"].strftime("%Y-%m-%d"): row["total"]
        for row in pred_rows
        if row["day"]
    }
    pred_data = [pred_map.get(k, 0) for k in date_keys]

    animal_predictions_chart = json.dumps({
        "labels": date_labels,
        "datasets": [{
            "label": "Species Identifications",
            "data": pred_data,
            "borderColor": "#3b82f6",
            "backgroundColor": "rgba(59, 130, 246, 0.15)",
            "fill": True,
            "tension": 0.35,
        }]
    })

    # Chart 3: Popular Animals (Top species identified)
    top_species_rows = (
        AnimalPrediction.objects.filter(identification_status="identified")
    )
    if start_date:
        top_species_rows = top_species_rows.filter(created_at__gte=start_date)

    top_species_rows = (
        top_species_rows.values("common_name")
        .annotate(count=Count("id"))
        .order_by("-count")[:7]
    )
    species_labels = [row["common_name"] for row in top_species_rows] or ["No Data Yet"]
    species_data = [row["count"] for row in top_species_rows] or [0]

    popular_animals_chart = json.dumps({
        "labels": species_labels,
        "datasets": [{
            "label": "Identified Specimens",
            "data": species_data,
            "backgroundColor": [
                "#10b981", "#059669", "#047857", "#065f46",
                "#34d399", "#6ee7b7", "#a7f3d0"
            ][:len(species_labels)],
        }]
    })

    # Chart 4: Payment Activity (Captured test orders count over time)
    pay_rows = (
        PaymentOrder.objects.filter(
            status="paid", created_at__date__gte=chart_start_date
        )
        .annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(total=Count("id"))
    )
    pay_map = {
        row["day"].strftime("%Y-%m-%d"): row["total"]
        for row in pay_rows
        if row["day"]
    }
    pay_data = [pay_map.get(k, 0) for k in date_keys]

    payment_activity_chart = json.dumps({
        "labels": date_labels,
        "datasets": [{
            "label": "Captured Test Orders",
            "data": pay_data,
            "borderColor": "#f59e0b",
            "backgroundColor": "rgba(245, 158, 11, 0.2)",
            "fill": True,
            "tension": 0.35,
        }]
    })

    # Chart 5: Identification Outcomes Distribution (Identified, Uncertain, Not Animal)
    outcomes_chart = json.dumps({
        "labels": ["Identified Species", "Uncertain Specimens", "Non-Animal Images"],
        "datasets": [{
            "label": "Outcome Count",
            "data": [identified_count, uncertain_count, not_animal_count],
            "backgroundColor": ["#10b981", "#f59e0b", "#ef4444"],
        }]
    })

    # Add all calculated items to context
    context.update({
        "kpi": {
            "period": period,
            "period_label": period_label,
            "total_users": total_users_count,
            "new_users_period": new_users_period_count,
            "total_predictions": total_predictions_count,
            "predictions_period": predictions_period_count,
            "identified_count": identified_count,
            "identified_rate": identified_rate,
            "uncertain_count": uncertain_count,
            "not_animal_count": not_animal_count,
            "total_conversations": total_conversations_count,
            "conversations_period": conversations_period_count,
            "total_messages": total_messages_count,
            "successful_payments": successful_captured_count,
            "pending_payments": pending_orders_count,
            "credits_purchased": credits_purchased_sum,
            "test_volume_rupees": test_volume_rupees,
        },
        "charts": {
            "user_registrations": user_registrations_chart,
            "animal_predictions": animal_predictions_chart,
            "popular_animals": popular_animals_chart,
            "payment_activity": payment_activity_chart,
            "identification_outcomes": outcomes_chart,
        },
    })
    return context

