# network/aws — VPC, subnets, egress allowlist (spec §14.5).
#
# Layout: one VPC, `az_count` AZs, three subnet tiers per AZ:
#   - public   (NAT gateways, load balancer ingress only)
#   - private  (control plane: orchestrator, MCP servers, Run Registry)
#   - gpu      (GPU node pool + sandbox-runtime; egress-allowlisted per §10.1)
#
# No secret values are read, written, or referenced anywhere in this module —
# only network topology. Nothing here belongs in `tofu state` that would
# violate spec §14.6.

terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.40"
    }
  }
}

locals {
  name = "${var.environment}-network"

  azs = slice(data.aws_availability_zones.available.names, 0, var.az_count)

  # /20s carved out of the /16 (or whatever cidr_block is): public, private,
  # gpu tiers, one of each per AZ. Sized generously for a pilot; production
  # environments pass a larger cidr_block and the math still holds as long
  # as az_count * 3 subnets fit.
  public_subnet_cidrs  = [for i, az in local.azs : cidrsubnet(var.cidr_block, 4, i)]
  private_subnet_cidrs = [for i, az in local.azs : cidrsubnet(var.cidr_block, 4, i + var.az_count)]
  gpu_subnet_cidrs     = [for i, az in local.azs : cidrsubnet(var.cidr_block, 4, i + (2 * var.az_count))]

  tags = merge(var.tags, {
    "sdlc-auto:environment" = var.environment
    "sdlc-auto:module"      = "network/aws"
    "sdlc-auto:tenant-id"   = coalesce(var.tenant_id, "shared")
  })
}

data "aws_availability_zones" "available" {
  state = "available"
}

resource "aws_vpc" "this" {
  cidr_block           = var.cidr_block
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = merge(local.tags, { Name = local.name })
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id
  tags   = merge(local.tags, { Name = "${local.name}-igw" })
}

resource "aws_subnet" "public" {
  count                   = var.az_count
  vpc_id                  = aws_vpc.this.id
  cidr_block              = local.public_subnet_cidrs[count.index]
  availability_zone       = local.azs[count.index]
  map_public_ip_on_launch = true

  tags = merge(local.tags, { Name = "${local.name}-public-${local.azs[count.index]}", Tier = "public" })
}

resource "aws_subnet" "private" {
  count             = var.az_count
  vpc_id            = aws_vpc.this.id
  cidr_block        = local.private_subnet_cidrs[count.index]
  availability_zone = local.azs[count.index]

  tags = merge(local.tags, { Name = "${local.name}-private-${local.azs[count.index]}", Tier = "private" })
}

resource "aws_subnet" "gpu" {
  count             = var.az_count
  vpc_id            = aws_vpc.this.id
  cidr_block        = local.gpu_subnet_cidrs[count.index]
  availability_zone = local.azs[count.index]

  tags = merge(local.tags, {
    Name = "${local.name}-gpu-${local.azs[count.index]}"
    Tier = "gpu-sandbox"
    # Karpenter (spec §14.4) discovers its subnets by this tag on the
    # EC2NodeClass; the gpu-node-pool/aws module's EC2NodeClass selects on it.
    "karpenter.sh/discovery" = var.environment
  })
}

# One NAT gateway per AZ (pilot can run with one; production wants
# per-AZ NAT for isolation from a single NAT gateway failure domain).
resource "aws_eip" "nat" {
  count  = var.az_count
  domain = "vpc"
  tags   = merge(local.tags, { Name = "${local.name}-nat-${local.azs[count.index]}" })
}

resource "aws_nat_gateway" "this" {
  count         = var.az_count
  allocation_id = aws_eip.nat[count.index].id
  subnet_id     = aws_subnet.public[count.index].id
  tags          = merge(local.tags, { Name = "${local.name}-nat-${local.azs[count.index]}" })

  depends_on = [aws_internet_gateway.this]
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this.id
  }
  tags = merge(local.tags, { Name = "${local.name}-public-rt" })
}

resource "aws_route_table_association" "public" {
  count          = var.az_count
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# Private and GPU tiers each get their own per-AZ route table so the GPU
# tier's default route can be swapped for an allowlisted egress path
# (below) without touching the control-plane private tier.
resource "aws_route_table" "private" {
  count  = var.az_count
  vpc_id = aws_vpc.this.id
  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.this[count.index].id
  }
  tags = merge(local.tags, { Name = "${local.name}-private-rt-${local.azs[count.index]}" })
}

resource "aws_route_table_association" "private" {
  count          = var.az_count
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private[count.index].id
}

resource "aws_route_table" "gpu" {
  count  = var.az_count
  vpc_id = aws_vpc.this.id
  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.this[count.index].id
  }
  tags = merge(local.tags, { Name = "${local.name}-gpu-rt-${local.azs[count.index]}" })
}

resource "aws_route_table_association" "gpu" {
  count          = var.az_count
  subnet_id      = aws_subnet.gpu[count.index].id
  route_table_id = aws_route_table.gpu[count.index].id
}

# --- Egress allowlist enforcement (spec §10.1, §14.5) -----------------------
#
# The GPU/sandbox tier's security group denies all egress by default and
# only opens the CIDRs in var.egress_allowlist, plus DNS/HTTPS to the
# in-VPC endpoints below. This is enforced at the infra level so no
# sandboxed agent process can exfiltrate to an arbitrary host regardless
# of what runs inside the sandbox runtime.
resource "aws_security_group" "gpu_egress_allowlist" {
  name_prefix = "${local.name}-gpu-egress-"
  description = "Egress allowlist for GPU node pool + sandbox-runtime (spec §10.1, §14.5)"
  vpc_id      = aws_vpc.this.id

  # No egress rule block here — all rules below are explicit allow entries.
  # Terraform/OpenTofu security groups default-deny anything not listed.

  tags = merge(local.tags, { Name = "${local.name}-gpu-egress-allowlist" })
}

resource "aws_vpc_security_group_egress_rule" "allowlisted" {
  for_each          = toset(var.egress_allowlist)
  security_group_id = aws_security_group.gpu_egress_allowlist.id
  cidr_ipv4         = each.value
  ip_protocol       = "-1"
  description       = "Allowlisted egress destination"
}

# Always allow HTTPS to the VPC's own private endpoints (S3 gateway
# endpoint below, plus any cloud API endpoint added later) so the model
# artifact store and secrets bootstrap remain reachable without opening
# the allowlist to the public internet for those paths.
resource "aws_vpc_security_group_egress_rule" "vpc_endpoints_https" {
  security_group_id = aws_security_group.gpu_egress_allowlist.id
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  prefix_list_id    = aws_vpc_endpoint.s3.prefix_list_id
  description       = "HTTPS to in-VPC S3 gateway endpoint (model artifact store, spec §13.5)"
}

resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.this.id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = concat(aws_route_table.gpu[*].id, aws_route_table.private[*].id)

  tags = merge(local.tags, { Name = "${local.name}-s3-endpoint" })
}
