variable "name" {
  description = "Name prefix for network resources."
  type        = string
}

variable "cidr_block" {
  description = "VPC CIDR block."
  type        = string
}

variable "azs" {
  description = "Availability zones to spread subnets across (two expected)."
  type        = list(string)

  validation {
    condition     = length(var.azs) == 2
    error_message = "Exactly two availability zones are expected."
  }
}

variable "enable_nat_gateway" {
  description = "Create a single NAT gateway for the private subnets."
  type        = bool
}
