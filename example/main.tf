# A very simple Terraform configuration that provides a single AWS instance.

# Normally a good habit would be to split this file into at least `main.tf`, `variables.tf` and `output.tf`, and to use
# template files instead of in-line literals for outputs. But hey, this is just a small self-contained example.

### Variables ###

variable "aws_region" {
  description = "The AWS region to use"
  default     = "eu-central-1"
}

variable "key_name" {
  description = "Name of the pre-existing AWS key pair to use"
}

variable "deployment_name" {
  description = "The deployment name, tagged in all resources"
}

### Data and tag template ###

provider "aws" {
  region = var.aws_region
  default_tags {
    tags = {
      DeploymentName = var.deployment_name
      Terraform      = "true"
    }
  }
}

data "aws_ami" "ubuntu" {
  owners = ["099720109477"]
  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-focal-20.04-amd64-server-*"]
  }
  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
  most_recent = true
}

### Resources ###

resource "aws_instance" "instance" {
  ami                         = data.aws_ami.ubuntu.id
  instance_type               = "t3.nano"
  key_name                    = var.key_name
  associate_public_ip_address = true
  tags = {
    Name = "${var.deployment_name} VM"
  }
}

### Outputs ###

data "template_file" "ssh_config" {
  template = <<EOT
Host *
  User $${system_user}
  StrictHostKeyChecking accept-new
  ControlMaster auto
  ControlPath ~/.ssh/cm_socket/%r@%h:%p
  ControlPersist 15s

Host instance-1
  HostName $${instance_ip}
EOT
  vars = {
    instance_ip = aws_instance.instance.public_ip
    system_user = "ubuntu"
  }
}

output "ssh_config" {
  description = "An example SSH configuration file to use with Ansible"
  value       = data.template_file.ssh_config.rendered
}

data "template_file" "ansible_hosts" {
  template = <<EOT
[instances]
instance-1
EOT
}

output "ansible_hosts" {
  description = "An Ansible host inventory file"
  value       = data.template_file.ansible_hosts.rendered
}
