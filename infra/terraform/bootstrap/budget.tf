resource "google_monitoring_notification_channel" "owner" {
  display_name = "Forseti budget alerts"
  type         = "email"
  labels = {
    email_address = var.owner_email
  }
  depends_on = [google_project_service.apis]
}

resource "google_billing_budget" "monthly" {
  billing_account = var.billing_account_id
  display_name    = "Forseti monthly budget"
  budget_filter {
    projects = ["projects/${data.google_project.project.number}"]
  }
  amount {
    specified_amount {
      currency_code = "EUR"
      units         = tostring(var.monthly_budget_eur)
    }
  }
  dynamic "threshold_rules" {
    for_each = [0.5, 0.9, 1.0]
    content {
      threshold_percent = threshold_rules.value
      spend_basis       = "CURRENT_SPEND"
    }
  }
  threshold_rules {
    threshold_percent = 1.0
    spend_basis       = "FORECASTED_SPEND"
  }
  all_updates_rule {
    monitoring_notification_channels = [google_monitoring_notification_channel.owner.id]
    disable_default_iam_recipients   = true
  }
  depends_on = [google_project_service.apis]
}
