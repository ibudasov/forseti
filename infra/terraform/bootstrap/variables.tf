variable "project_id" {
  type    = string
  default = "forseti-505116"
}

variable "region" {
  type    = string
  default = "europe-west4"
}

variable "owner_email" {
  type      = string
  sensitive = true
}

variable "billing_account_id" {
  type      = string
  sensitive = true
}

variable "github_repository" {
  type    = string
  default = "ibudasov/forseti"
}

variable "github_repository_id" {
  type    = string
  default = "1323914036"
}

variable "github_owner_id" {
  type    = string
  default = "3657892"
}

variable "monthly_budget_eur" {
  type    = number
  default = 30
  validation {
    condition     = var.monthly_budget_eur > 0 && floor(var.monthly_budget_eur) == var.monthly_budget_eur
    error_message = "The monthly budget must be a positive whole number of euros."
  }
}
