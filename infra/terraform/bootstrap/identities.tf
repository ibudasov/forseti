locals {
  project_roles = merge([
    for name, account in local.accounts : {
      for role in account.project_roles : "${name}/${role}" => { name = name, role = role }
    }
  ]...)
  conditional_project_roles = merge([
    for name, account in local.accounts : {
      for grant in lookup(account, "conditional_project_roles", []) :
      "${name}/${grant.role}" => { name = name, role = grant.role, condition = grant.condition }
    }
  ]...)
  act_as = merge([
    for name, account in local.accounts : {
      for target in account.act_as : "${name}/${target}" => { name = name, target = target }
    }
  ]...)
}

resource "google_service_account" "accounts" {
  for_each     = local.accounts
  account_id   = each.key
  display_name = each.key
  description  = each.value.purpose
  depends_on   = [google_project_service.apis]
}

resource "google_project_iam_member" "project_roles" {
  for_each = local.project_roles
  project  = var.project_id
  role     = "roles/${each.value.role}"
  member   = "serviceAccount:${local.account_emails[each.value.name]}"
}

resource "google_project_iam_member" "storage_except_state" {
  for_each = local.conditional_project_roles
  project  = var.project_id
  role     = "roles/${each.value.role}"
  member   = "serviceAccount:${local.account_emails[each.value.name]}"
  lifecycle {
    precondition {
      condition     = each.value.condition == "exclude_state_bucket"
      error_message = "Unsupported conditional project grant in identities.json."
    }
  }
  condition {
    title = "exclude_state_bucket"
    # Bucket IAM changes and object operations use different resource names.
    expression = "!resource.name.startsWith('projects/_/buckets/${local.state_bucket}')"
  }
}

resource "google_service_account_iam_member" "act_as" {
  for_each           = local.act_as
  service_account_id = google_service_account.accounts[each.value.target].name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${local.account_emails[each.value.name]}"
}
