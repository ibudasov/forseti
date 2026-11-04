locals {
  analysis_completed_filter = "jsonPayload.event=\"analysis_completed\""
  agent_degraded_filter     = "jsonPayload.event=\"agent_degraded\""
  agent_downgraded_filter   = "jsonPayload.event=\"agent_downgraded\""
  refresh_status_filter     = "jsonPayload.event=\"daily_refresh_status\""
}

resource "google_logging_metric" "analysis_count" {
  name   = "forseti/analysis_count"
  filter = local.analysis_completed_filter

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"

    labels {
      key         = "decision"
      value_type  = "STRING"
      description = "Final recommendation decision."
    }
    labels {
      key         = "pipeline"
      value_type  = "STRING"
      description = "Analysis pipeline used."
    }
    labels {
      key         = "cost_status"
      value_type  = "STRING"
      description = "Coverage status of the pricing catalog."
    }
  }

  label_extractors = {
    decision    = "EXTRACT(jsonPayload.decision)"
    pipeline    = "EXTRACT(jsonPayload.pipeline)"
    cost_status = "EXTRACT(jsonPayload.cost_status)"
  }
}

resource "google_logging_metric" "analysis_latency_ms" {
  name   = "forseti/analysis_latency_ms"
  filter = local.analysis_completed_filter

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "DISTRIBUTION"
    unit        = "ms"
  }

  value_extractor = "EXTRACT(jsonPayload.total_latency_ms)"
  bucket_options {
    explicit_buckets {
      bounds = [100, 250, 500, 1000, 2500, 5000, 10000, 30000, 60000]
    }
  }
}

resource "google_logging_metric" "analysis_cost_usd" {
  name   = "forseti/analysis_cost_usd"
  filter = "${local.analysis_completed_filter} AND jsonPayload.cost_usd>=0"

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "DISTRIBUTION"
    unit        = "USD"
  }

  value_extractor = "EXTRACT(jsonPayload.cost_usd)"
  bucket_options {
    explicit_buckets {
      bounds = [0.0001, 0.001, 0.01, 0.1, 0.5, 1, 2, 5, 10, 25]
    }
  }
}

resource "google_logging_metric" "analysis_confidence" {
  name   = "forseti/analysis_confidence"
  filter = local.analysis_completed_filter

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "DISTRIBUTION"
    unit        = "1"
  }

  value_extractor = "EXTRACT(jsonPayload.confidence)"
  bucket_options {
    explicit_buckets {
      bounds = [0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1]
    }
  }
}

resource "google_logging_metric" "analysis_degraded_count" {
  name   = "forseti/analysis_degraded_count"
  filter = "${local.analysis_completed_filter} AND jsonPayload.degraded=true"

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"
  }
}

resource "google_logging_metric" "agent_degraded_count" {
  name   = "forseti/agent_degraded_count"
  filter = local.agent_degraded_filter

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"

    labels {
      key         = "agent"
      value_type  = "STRING"
      description = "Agent that returned a degraded result."
    }
    labels {
      key         = "reason"
      value_type  = "STRING"
      description = "Sanitized degradation reason."
    }
  }

  label_extractors = {
    agent  = "EXTRACT(jsonPayload.agent)"
    reason = "EXTRACT(jsonPayload.reason)"
  }
}

resource "google_logging_metric" "agent_downgrade_count" {
  name   = "forseti/agent_downgrade_count"
  filter = local.agent_downgraded_filter

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"

    labels {
      key         = "agent"
      value_type  = "STRING"
      description = "Agent that applied a downgrade."
    }
  }

  label_extractors = {
    agent = "EXTRACT(jsonPayload.agent)"
  }
}

resource "google_logging_metric" "daily_refresh_status" {
  name   = "forseti/daily_refresh_status"
  filter = local.refresh_status_filter

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"

    labels {
      key         = "status"
      value_type  = "STRING"
      description = "Refresh completion status."
    }
  }

  label_extractors = {
    status = "EXTRACT(jsonPayload.status)"
  }
}

resource "google_monitoring_notification_channel" "owner_email" {
  display_name = "Forseti owner email"
  type         = "email"
  enabled      = true

  labels = {
    email_address = var.owner_email
  }
}

resource "google_monitoring_dashboard" "forseti" {
  dashboard_json = file("${path.module}/../dashboards/forseti.json")
}

resource "google_monitoring_alert_policy" "refresh_failed" {
  depends_on = [google_logging_metric.daily_refresh_status]

  display_name          = "Forseti daily refresh failed"
  combiner              = "OR"
  enabled               = true
  notification_channels = [google_monitoring_notification_channel.owner_email.name]

  conditions {
    display_name = "Refresh status is failed"
    condition_threshold {
      filter          = "metric.type=\"logging.googleapis.com/user/forseti/daily_refresh_status\" AND resource.type=\"cloud_run_job\" AND metric.label.status=\"failed\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "0s"
      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_SUM"
      }
      trigger {
        count = 1
      }
    }
  }
}

resource "google_monitoring_alert_policy" "refresh_missing" {
  depends_on = [google_logging_metric.daily_refresh_status]

  display_name          = "Forseti daily refresh missing"
  combiner              = "OR"
  enabled               = true
  notification_channels = [google_monitoring_notification_channel.owner_email.name]

  conditions {
    display_name = "No successful refresh in 23h30m"
    condition_absent {
      filter   = "metric.type=\"logging.googleapis.com/user/forseti/daily_refresh_status\" AND resource.type=\"cloud_run_job\" AND metric.label.status=\"success\""
      duration = "84600s"
      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }
}

resource "google_monitoring_alert_policy" "api_5xx" {
  display_name          = "Forseti API returned a 5xx"
  combiner              = "OR"
  enabled               = true
  notification_channels = [google_monitoring_notification_channel.owner_email.name]

  conditions {
    display_name = "Cloud Run 5xx responses"
    condition_threshold {
      filter          = "metric.type=\"run.googleapis.com/request_count\" AND resource.type=\"cloud_run_revision\" AND metric.label.response_code_class=\"5xx\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "0s"
      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_SUM"
      }
      trigger {
        count = 1
      }
    }
  }
}

resource "google_monitoring_alert_policy" "sql_disk" {
  display_name          = "Forseti Cloud SQL disk above 80 percent"
  combiner              = "OR"
  enabled               = true
  notification_channels = [google_monitoring_notification_channel.owner_email.name]

  conditions {
    display_name = "Cloud SQL disk utilization"
    condition_threshold {
      filter          = "metric.type=\"cloudsql.googleapis.com/database/disk/utilization\" AND resource.type=\"cloudsql_database\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0.8
      duration        = "300s"
      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_MEAN"
      }
      trigger {
        count = 1
      }
    }
  }
}

resource "google_monitoring_alert_policy" "sql_connections" {
  display_name          = "Forseti Cloud SQL connections above 20"
  combiner              = "OR"
  enabled               = true
  notification_channels = [google_monitoring_notification_channel.owner_email.name]

  conditions {
    display_name = "Cloud SQL active connections"
    condition_threshold {
      filter          = "metric.type=\"cloudsql.googleapis.com/database/postgresql/num_backends\" AND resource.type=\"cloudsql_database\""
      comparison      = "COMPARISON_GT"
      threshold_value = 20
      duration        = "300s"
      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_MEAN"
      }
      trigger {
        count = 1
      }
    }
  }
}

resource "google_monitoring_alert_policy" "daily_llm_cost" {
  depends_on = [google_logging_metric.analysis_cost_usd]

  display_name          = "Forseti daily LLM cost exceeds threshold"
  combiner              = "OR"
  enabled               = true
  notification_channels = [google_monitoring_notification_channel.owner_email.name]

  conditions {
    display_name = "Daily recommendation cost"
    condition_monitoring_query_language {
      query    = <<-EOT
        fetch logging.googleapis.com/user/forseti/analysis_cost_usd
        | filter resource.type == 'cloud_run_revision'
        | group_by 1d, [], [daily_cost_usd: sum(value.distribution.mean * value.distribution.count)]
        | condition daily_cost_usd > ${var.daily_llm_cost_threshold_usd}
      EOT
      duration = "0s"
      trigger {
        count = 1
      }
    }
  }
}

resource "google_monitoring_alert_policy" "unpriced_run" {
  depends_on = [google_logging_metric.analysis_count]

  display_name          = "Forseti analysis has unpriced model usage"
  combiner              = "OR"
  enabled               = true
  notification_channels = [google_monitoring_notification_channel.owner_email.name]

  conditions {
    display_name = "Analysis cost status is not priced"
    condition_threshold {
      filter          = "metric.type=\"logging.googleapis.com/user/forseti/analysis_count\" AND resource.type=\"cloud_run_revision\" AND metric.label.cost_status!=\"priced\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "0s"
      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_SUM"
      }
      trigger {
        count = 1
      }
    }
  }
}
