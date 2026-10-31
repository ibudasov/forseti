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

variable "credentials_version" {
  description = "Bump to rotate the database passwords; all write-only credentials rotate together."
  type        = number
  default     = 1
}
