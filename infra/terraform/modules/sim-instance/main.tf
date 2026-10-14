locals {
  name = "${var.project_name}-${var.environment}-sim"

  tags = merge(
    {
      Project     = var.project_name
      Environment = var.environment
      Component   = "simulation"
      ManagedBy   = "terraform"
    },
    var.tags,
  )

  # AMI detectat automat doar când nu este dat unul explicit.
  use_lookup_ami = var.ami_id == ""

  has_existing_key = var.ssh_key_name != ""
  create_key       = !local.has_existing_key && var.ssh_key_public_key_path != ""

  assets_bucket_arns = var.assets_bucket_name == "" ? [] : ["arn:aws:s3:::${var.assets_bucket_name}"]

  key_name = local.has_existing_key ? var.ssh_key_name : (
    local.create_key ? aws_key_pair.this[0].key_name : null
  )

  # Chelie publică citită de Terraform ca să poată fi comparată și reîncărcată la schimbare.
  public_key = local.create_key ? file(pathexpand(var.ssh_key_public_key_path)) : ""

  # Porturile WebRTC directe se deschid doar către CIDR-urile deja permise (aceleași ca la SSH,
  # de regulă IP-ul operatorului). Fără ele, singura cale de media ar fi TURN.
  webrtc_cidrs = length(var.ssh_allowed_cidrs) > 0 ? var.ssh_allowed_cidrs : ["0.0.0.0/0"]

  # Cu Client VPN pornit, porturile de streaming se deschid doar către pulul de adrese al
  # tunelului. Traficul ajunge la instanță cu adresa de tunel a clientului, nu cu cea publică.
  vpn_cidrs = var.vpn_client_cidr_block == "" ? [] : [var.vpn_client_cidr_block]

  # Volumul de date este legat de o singură zonă, deci instanța trebuie să pornească în zona
  # lui. Subnetul se alege în trei trepte: unul dat explicit, primul subnet public din zona
  # cerută, ori primul subnet public găsit.
  subnet_id = var.subnet_id != "" ? var.subnet_id : (
    var.availability_zone != "" ? data.aws_subnets.public_az[0].ids[0] : data.aws_subnets.public[0].ids[0]
  )
}

data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

data "aws_vpc" "default" {
  count   = var.vpc_id == "" ? 1 : 0
  default = true
}

data "aws_vpc" "selected" {
  count = var.vpc_id == "" ? 0 : 1
  id    = var.vpc_id
}

data "aws_subnets" "public" {
  count = var.subnet_id == "" && var.availability_zone == "" ? 1 : 0

  filter {
    name   = "vpc-id"
    values = [var.vpc_id == "" ? data.aws_vpc.default[0].id : data.aws_vpc.selected[0].id]
  }
  filter {
    name   = "map-public-ip-on-launch"
    values = ["true"]
  }
  filter {
    name   = "state"
    values = ["available"]
  }
}

# Subnetul public din zona volumului de date. Volumul EBS este legat de o zonă, deci instanța
# trebuie să pornească exact acolo ca să îl poată atașa.
data "aws_subnets" "public_az" {
  count = var.subnet_id == "" && var.availability_zone != "" ? 1 : 0

  filter {
    name   = "vpc-id"
    values = [var.vpc_id == "" ? data.aws_vpc.default[0].id : data.aws_vpc.selected[0].id]
  }
  filter {
    name   = "availability-zone"
    values = [var.availability_zone]
  }
  filter {
    name   = "map-public-ip-on-launch"
    values = ["true"]
  }
  filter {
    name   = "state"
    values = ["available"]
  }
}

data "aws_ami" "gpu" {
  count       = local.use_lookup_ami ? 1 : 0
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = [var.ami_name_pattern]
  }
  filter {
    name   = "architecture"
    values = ["x86_64"]
  }
  filter {
    name   = "root-device-type"
    values = ["ebs"]
  }
  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
  filter {
    name   = "state"
    values = ["available"]
  }
}

# ---------------------------------------------------------------- cheie SSH
resource "aws_key_pair" "this" {
  count = local.create_key ? 1 : 0

  key_name   = "${local.name}-key"
  public_key = local.public_key

  tags = local.tags

  lifecycle {
    precondition {
      condition     = can(regex("^(ssh-rsa|ssh-ed25519|ecdsa-sha2-nistp256) ", trimspace(local.public_key)))
      error_message = "Fișierul indicat de ssh_key_public_key_path nu conține o cheie publică SSH validă."
    }
  }
}

# ---------------------------------------------------------------- rețea
resource "aws_security_group" "sim" {
  name_prefix = "${local.name}-"
  description = "Isaac Sim: acces de administrare si porturi de streaming WebRTC."
  vpc_id      = var.vpc_id == "" ? data.aws_vpc.default[0].id : var.vpc_id

  tags = merge(local.tags, { Name = "${local.name}-sg" })

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_security_group_ingress_rule" "ssh" {
  for_each = toset(var.ssh_allowed_cidrs)

  security_group_id = aws_security_group.sim.id
  description       = "SSH din ${each.value}"
  cidr_ipv4         = each.value
  from_port         = 22
  to_port           = 22
  ip_protocol       = "tcp"

  tags = local.tags
}

# Serverul DCV ascultă pe 8443. Implicit rămâne închis public și se ajunge la el prin tunel
# SSH (`ssh -L 8443:localhost:8443`), care oricum este autentificat cu cheie.
resource "aws_vpc_security_group_ingress_rule" "dcv" {
  for_each = var.enable_dcv && var.dcv_public_access ? toset(local.webrtc_cidrs) : toset([])

  security_group_id = aws_security_group.sim.id
  description       = "Amazon DCV din ${each.value}"
  cidr_ipv4         = each.value
  from_port         = var.dcv_port
  to_port           = var.dcv_port
  ip_protocol       = "tcp"

  tags = local.tags
}

resource "aws_vpc_security_group_ingress_rule" "signaling" {
  for_each = var.webrtc_public_ports ? toset(local.webrtc_cidrs) : toset([])

  security_group_id = aws_security_group.sim.id
  description       = "Semnalizare WebRTC Isaac Sim"
  cidr_ipv4         = each.value
  from_port         = var.signaling_port
  to_port           = var.signaling_port
  ip_protocol       = "tcp"

  tags = local.tags
}

# Media WebRTC este UDP, iar Isaac Sim 5.0 o alocă dintr-un interval (47998-48020), nu pe un
# singur port. O regulă TCP pe 49100 nu lasă fluxul video să treacă: acolo ascultă semnalizarea.
resource "aws_vpc_security_group_ingress_rule" "media" {
  for_each = var.webrtc_public_ports ? toset(local.webrtc_cidrs) : toset([])

  security_group_id = aws_security_group.sim.id
  description       = "Media WebRTC Isaac Sim, UDP ${var.media_port_range}"
  cidr_ipv4         = each.value
  from_port         = tonumber(split("-", var.media_port_range)[0])
  to_port           = tonumber(split("-", var.media_port_range)[1])
  ip_protocol       = "udp"

  tags = local.tags
}

# ---------------------------------------------------------------- acces prin Client VPN
# Numai dacă tunelul este activ. Fără el, aceste reguli nu există și porturile rămân închise.
resource "aws_vpc_security_group_ingress_rule" "vpn_ssh" {
  for_each = toset(local.vpn_cidrs)

  security_group_id = aws_security_group.sim.id
  description       = "SSH prin tunelul Client VPN"
  cidr_ipv4         = each.value
  from_port         = 22
  to_port           = 22
  ip_protocol       = "tcp"

  tags = local.tags
}

resource "aws_vpc_security_group_ingress_rule" "vpn_signaling" {
  for_each = toset(local.vpn_cidrs)

  security_group_id = aws_security_group.sim.id
  description       = "Semnalizare WebRTC prin tunelul Client VPN"
  cidr_ipv4         = each.value
  from_port         = var.signaling_port
  to_port           = var.signaling_port
  ip_protocol       = "tcp"

  tags = local.tags
}

resource "aws_vpc_security_group_ingress_rule" "vpn_media" {
  for_each = toset(local.vpn_cidrs)

  security_group_id = aws_security_group.sim.id
  description       = "Media WebRTC prin tunelul Client VPN, UDP ${var.media_port_range}"
  cidr_ipv4         = each.value
  from_port         = tonumber(split("-", var.media_port_range)[0])
  to_port           = tonumber(split("-", var.media_port_range)[1])
  ip_protocol       = "udp"

  tags = local.tags
}

resource "aws_vpc_security_group_ingress_rule" "turn_listener" {
  for_each = var.enable_turn ? toset(["tcp", "udp"]) : toset([])

  security_group_id = aws_security_group.sim.id
  description       = "TURN/STUN listener (${each.value})"
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 3478
  to_port           = 3478
  ip_protocol       = each.value

  tags = local.tags
}

resource "aws_vpc_security_group_ingress_rule" "turn_relay" {
  count = var.enable_turn ? 1 : 0

  security_group_id = aws_security_group.sim.id
  description       = "TURN relay UDP"
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = tonumber(split("-", var.turn_relay_port_range)[0])
  to_port           = tonumber(split("-", var.turn_relay_port_range)[1])
  ip_protocol       = "udp"

  tags = local.tags
}

resource "aws_vpc_security_group_egress_rule" "all" {
  security_group_id = aws_security_group.sim.id
  description       = "Trafic de iesire nelimitat (descarcare imagini, S3, IoT)"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"

  tags = local.tags
}

# ---------------------------------------------------------------- IAM
data "aws_iam_policy_document" "assume_ec2" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "sim" {
  name               = "${local.name}-role"
  assume_role_policy = data.aws_iam_policy_document.assume_ec2.json
  description        = "Rolul instantei de simulare: SSM, S3 si IoT."

  tags = local.tags
}

# Acces administrativ prin Session Manager, fără port SSH deschis.
resource "aws_iam_role_policy_attachment" "ssm_core" {
  role       = aws_iam_role.sim.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy_attachment" "cloudwatch_agent" {
  count = var.install_cloudwatch_agent ? 1 : 0

  role       = aws_iam_role.sim.name
  policy_arn = "arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy"
}

data "aws_iam_policy_document" "sim_inline" {
  # Citirea asset-urilor de simulare din bucketul dedicat sau din orice bucket de platformă.
  dynamic "statement" {
    for_each = length(local.assets_bucket_arns) > 0 ? [1] : []

    content {
      sid       = "AssetsReadBucket"
      effect    = "Allow"
      actions   = ["s3:ListBucket", "s3:GetBucketLocation", "s3:ListBucketMultipartUploads"]
      resources = local.assets_bucket_arns
    }
  }

  dynamic "statement" {
    for_each = length(local.assets_bucket_arns) > 0 ? [1] : []

    content {
      sid    = "AssetsReadObjects"
      effect = "Allow"

      actions = [
        "s3:GetObject",
        "s3:GetObjectVersion",
        "s3:GetObjectAttributes",
        "s3:ListMultipartUploadParts",
        "s3:AbortMultipartUpload",
      ]

      resources = [for arn in local.assets_bucket_arns : "${arn}/*"]
    }
  }

  # Scrierea telemetriei, snapshot-urilor și artefactelor în data lake.
  dynamic "statement" {
    for_each = length(var.data_lake_bucket_arns) > 0 ? [1] : []

    content {
      sid       = "DataLakeListBucket"
      effect    = "Allow"
      actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
      resources = var.data_lake_bucket_arns
    }
  }

  dynamic "statement" {
    for_each = length(var.data_lake_bucket_arns) > 0 ? [1] : []

    content {
      sid    = "DataLakeWriteObjects"
      effect = "Allow"

      actions = [
        "s3:PutObject",
        "s3:PutObjectTagging",
        "s3:AbortMultipartUpload",
        "s3:ListMultipartUploadParts",
      ]

      resources = [for arn in var.data_lake_bucket_arns : "${arn}/*"]
    }
  }

  statement {
    sid       = "IoTConnect"
    effect    = "Allow"
    actions   = ["iot:Connect"]
    resources = ["arn:aws:iot:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:client/${var.project_name}-*"]
  }

  statement {
    sid    = "IoTPublishTelemetry"
    effect = "Allow"

    actions = [
      "iot:Publish",
      "iot:Receive",
      "iot:Subscribe",
      "iot:RetainPublish",
    ]

    resources = length(var.iot_publish_resource_arns) > 0 ? var.iot_publish_resource_arns : [
      "arn:aws:iot:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:topic/${var.project_name}/*",
      "arn:aws:iot:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:topicfilter/${var.project_name}/*",
    ]
  }

  statement {
    sid    = "MetricsAndLogs"
    effect = "Allow"

    actions = [
      "cloudwatch:PutMetricData",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
      "logs:DescribeLogStreams",
    ]

    resources = ["*"]
  }

  # Parola sesiunii DCV: generată o singură dată la bootstrap și păstrată ca SecureString, ca
  # să nu ajungă nici în user_data, nici în state-ul Terraform.
  dynamic "statement" {
    for_each = var.enable_dcv ? [1] : []

    content {
      sid    = "DcvPasswordParameter"
      effect = "Allow"

      actions = [
        "ssm:GetParameter",
        "ssm:PutParameter",
      ]

      resources = [
        "arn:aws:ssm:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:parameter/${local.name}/dcv-password",
      ]
    }
  }

  # Licența DCV: pe EC2 serverul nu are nevoie de license server, dar își verifică periodic
  # licența într-un bucket administrat de AWS, `dcv-license.<regiune>`. Fără citire, DCV cade
  # pe licența de evaluare de 30 de zile, iar după expirare nu mai poate ține sesiuni.
  dynamic "statement" {
    for_each = var.enable_dcv ? [1] : []

    content {
      sid       = "DcvLicenseObject"
      effect    = "Allow"
      actions   = ["s3:GetObject"]
      resources = ["arn:aws:s3:::dcv-license.${data.aws_region.current.name}/*"]
    }
  }
}

resource "aws_iam_role_policy" "sim_inline" {
  name   = "${local.name}-inline"
  role   = aws_iam_role.sim.id
  policy = data.aws_iam_policy_document.sim_inline.json
}

resource "aws_iam_instance_profile" "sim" {
  name = "${local.name}-profile"
  role = aws_iam_role.sim.name

  tags = local.tags
}

# ---------------------------------------------------------------- instanță
resource "aws_instance" "sim" {
  ami                         = local.use_lookup_ami ? data.aws_ami.gpu[0].id : var.ami_id
  instance_type               = var.instance_type
  subnet_id                   = local.subnet_id
  vpc_security_group_ids      = [aws_security_group.sim.id]
  iam_instance_profile        = aws_iam_instance_profile.sim.name
  key_name                    = local.key_name
  associate_public_ip_address = var.associate_public_ip

  user_data_replace_on_change = true
  # Scriptul depășește limita de 16 KiB a API-ului EC2 dacă e trimis ca text simplu, iar
  # `user_data` ar fi codat încă o dată de provider (instanța ar primi base64 în loc de shell).
  # `user_data_base64` e transmis ca atare, deci comprimăm cu gzip și lăsăm base64-ul rezultat:
  # cloud-init detectează antetul gzip și decomprimă singur.
  user_data_base64 = base64gzip(replace(
    file("${path.module}/templates/user_data.sh.tftpl"),
    "__CNCDT_CONFIG_B64__",
    base64encode(jsonencode({
      project                   = var.project_name
      environment               = var.environment
      assets_bucket             = var.assets_bucket_name
      isaac_image               = var.isaac_sim_image
      entrypoint_script         = var.isaac_sim_entrypoint_script
      signaling_port            = var.signaling_port
      stream_port               = var.stream_port
      enable_turn               = var.enable_turn
      turn_relay_range          = var.turn_relay_port_range
      autostart_sim             = var.autostart_sim
      install_cloudwatch_agent  = var.install_cloudwatch_agent
      enable_dcv                = var.enable_dcv
      dcv_port                  = var.dcv_port
      dcv_password_parameter    = "/${local.name}/dcv-password"
      livestream_public_address = var.livestream_public_address
      isaac_content_mirror      = var.isaac_content_mirror
      isaac_content_url         = var.isaac_content_url
      isaac_content_sections    = var.isaac_content_sections
      data_volume_id            = var.data_volume_id
      extra_commands            = var.extra_bootstrap_commands
    }))
  ))

  # Piața spot: aproximativ jumătate din tariful on-demand. Cererea este `one-time` cu
  # `instance_interruption_behavior = "terminate"`, adică la o întrerupere AWS anunță cu două
  # minute, termină instanța și nu mai relansează nimic de la sine: cineva decide, cu
  # `make sim-up`, când se pornește din nou (din AMI-ul golden, cu Isaac Sim deja instalat).
  # O cerere `persistent` cu oprire ar păstra discul la întrerupere, dar AWS ar reporni singură
  # instanța când reapare capacitatea, fără ca nimeni să fi cerut-o.
  dynamic "instance_market_options" {
    for_each = var.use_spot ? [1] : []

    content {
      market_type = "spot"

      spot_options {
        spot_instance_type             = "one-time"
        instance_interruption_behavior = "terminate"
      }
    }
  }

  root_block_device {
    volume_type           = "gp3"
    volume_size           = var.root_volume_size_gb
    iops                  = var.root_volume_iops
    throughput            = var.root_volume_throughput
    encrypted             = true
    kms_key_id            = var.root_volume_kms_key_id == "" ? null : var.root_volume_kms_key_id
    delete_on_termination = true
  }

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
  }

  # Instanța de simulare este efemeră: nu pornim pe ea servicii de producție.
  monitoring = true

  tags = merge(local.tags, { Name = local.name })

  lifecycle {
    ignore_changes = [ami]
  }
}

resource "aws_eip" "sim" {
  count = var.allocate_eip ? 1 : 0

  domain   = "vpc"
  instance = aws_instance.sim.id

  tags = merge(local.tags, { Name = "${local.name}-eip" })

  depends_on = [aws_instance.sim]
}

# Volumul de date, în stack separat (`sim-data`), supraviețuiește instanței: aici doar îl
# atașăm. La on-demand instanța este oprită înainte de detașare, ca ext4 să își încheie
# jurnalul. Pe spot (`one-time`) oprirea ar termina instanța, iar așteptarea stării „stopped”
# ar dura degeaba: se detașează direct, iar jurnalul de pe volum se reia la montarea următoare.
resource "aws_volume_attachment" "data" {
  count = var.data_volume_id == "" ? 0 : 1

  device_name                    = var.data_volume_device_name
  volume_id                      = var.data_volume_id
  instance_id                    = aws_instance.sim.id
  stop_instance_before_detaching = !var.use_spot
}

# ---------------------------------------------------------------- CloudWatch
resource "aws_cloudwatch_log_group" "bootstrap" {
  name              = "/aws/ec2/${local.name}/bootstrap"
  retention_in_days = var.environment == "prod" ? 30 : 7

  tags = local.tags
}

resource "aws_cloudwatch_metric_alarm" "status_check" {
  alarm_name          = "${local.name}-status-check-failed"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "StatusCheckFailed"
  namespace           = "AWS/EC2"
  period              = 300
  statistic           = "Maximum"
  threshold           = 0
  alarm_description   = "Verificările de status ale instanței de simulare au eșuat."

  dimensions = {
    InstanceId = aws_instance.sim.id
  }

  treat_missing_data = "notBreaching"

  tags = local.tags
}
