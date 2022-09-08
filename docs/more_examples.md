# Trivial example

In practice when you have a suitable Terraform configuration and an Ansible playbook, you can glue them together with a
`dream.py`, in which you implement a module inheriting `dreamer.Module`. As a very trivial example relying heavily on
Dreamer's opinionated defaults:

```python
class ExampleModule(Module):
    friendly_name = 'example'
```

Dreamer has some requirements for Terraform configurations: most notably, your Terraform configuration must have an
output which is a valid Ansible inventory file. By default this output should be named `ansible_hosts`, but like most
things in Dreamer, this can be overridden on a per-module basis.

A more real-world example of a Dreamer configuration while with a custom Ansible playbok path, a module dependency,
and additional custom steps.

```python
class LogParserModule(Module):
    friendly_name = 'logparser'
    playbook_path = 'ansible/playbook.yml'
    depends_on = 'deployment-vpc'
    steps = {
        **Module.steps,
        'tunnel': 'tunnel',
        'exit': 'exit',
    }

    def tunnel(self):
        # omitted for brevity

    def exit(self):
        # omitted for brevity
```

# Case study: Deploying forensic analysis infrastructure

As a more practical example, let's set up an AWS VPC with a flat network and a security group allowing access only from
our own IP address. After that, we will write a Dreamer module and associated files which can be used to deploy hosts
for forensic analysis on the Linux command line.

The documentation does not include a full Ansible playbook or a Terraform configuration, but excerpts of the critical
parts are given.

This setup, contrived as it may be, could be useful for prototyping forensic tooling or for an independent researcher
who does not want to pay for the storage of an AMI, instead opting for a short setup time for each case.

Starting with the Terraform configuration for a flat network: (excerpt from `analysis-vpc/main.tf`):

```terraform
variable "vpc_cidr" {
  description = "The CIDR to use for the VPC"
}

resource "aws_vpc" "vpc" {
  cidr_block = var.vpc_cidr
}

resource "aws_internet_gateway" "igw" {
  vpc_id = aws_vpc.vpc.id
}

output "submodule_export" {
  description = "Terraform variables for Dreamer submodules to use"
  value       = <<EOT
vpc_id="${aws_vpc.vpc.id}"
EOT
}
```

As you can see, most of the configuration is ordinary, with the only Dreamer-specific detail being the
`submodule_export` output. The output is formatted as a valid Terraform variable file, which will subsequently be used
by the second Dreamer module we write later.

The first Dreamer module will be very simple (`analysis-vpc/dream.py`):

```python
from dreamer import Module

class AnalysisVPCModule(Module):
    friendly_name = 'analysis-vpc'
    default_steps = ['plan', 'apply', 'output']  # skip Ansible, as there's nothing to do there
    outputs = {
        'submodule_export': 'exported.tfvars'
    }
    export_outputs = 'submodule_export'
```

The important parts here are:

* Removing Ansible from the default steps, as for a pure Terraform module we don't need to ever run Ansible
* Storing the Terraform output named `submodule_export` in a file by defining it in `Module.outputs`
* Making the `submodule_export` output available to child modules by setting `Module.submodule_export` to it [^1]

(In case you are curious, you can also set `Module.submodule_exports` to a tuple to export multiple outputs.)

If you have valid AWS credentials available in `~/.aws/credentials`, you should now be able to deploy the network using
Dreamer. Presuming you saved the file in `~/code/dreamer-modules/analysis-vpc/`:

```sh
$ dream -b ~/.dreamer-state -b ~/code/dreamer-modules analysis-vpc test
```

Writing the `-d` and `-b` parameters can get cumbersome, so as an aside let's do the following and presume them for the
rest of this example:

```sh
$ export DREAMER_MODULE_REPOSITORY=$HOME/code/dreamer-modules
$ export DREAMER_BASE_DIR=$HOME/.dreamer-state
```

After this running Dreamer is significantly cleaner:

```sh
$ dream analysis-vpc test
```

Leave the VPC deployed for now. For future reference, when you want to destroy the resources, use the `-o` parameter
and specify the default `destroy` step, which simply runs `terraform destroy` on the resources.

```sh
$ dream analysis-vpc test -o destroy
```

The second module will contain more things, but quoting the critical things here (following excerpts from
`analysis-host/main.tf`). First let's set up some variables to refer to the parent module and make the configuration
more useful:

```terraform
variable "vpc_id" {
  description = "The VPC id to use"
}

variable "vm_count" {
  description = "The amount of virtual machines to set up"
  default     = 1
}

variable "instance_type" {
  description = "The instance type to use"
  default = "t3.medium"
}

variable "key_name" {
  description = "The AWS key name to use for initial access"
}

variable "aws_az" {
  description = "The AWS availability zone to use"
}
```

Remember that in the `analysis-vpc` output called `submodule_export` we created a valid Terraform variable file which
specifies the `vpc_id` variable.

Then let's have Terraform actually refer to the VPC and fetch the ID of the latest official Ubuntu Server 20.04 AMI:

```terraform
data "aws_vpc" "default" {
  id = var.vpc_id
}

data "aws_internet_gateway" "default" {
  filter {
    name   = "attachment.vpc-id"
    values = [var.vpc_id]
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
```

Next let's set up some networking for our instance(s):

```terraform
resource "aws_subnet" "subnet" {
  cidr_block              = var.cidr
  vpc_id                  = data.aws_vpc.default.id
  availability_zone       = var.aws_az
  map_public_ip_on_launch = true
}

resource "aws_route_table" "subnet_routes" {
  vpc_id = data.aws_vpc.default.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = data.aws_internet_gateway.default.id
  }
}

resource "aws_route_table_association" "subnet_route_association" {
  subnet_id      = aws_subnet.subnet.id
  route_table_id = aws_route_table.subnet_routes.id
}

resource "aws_security_group" "sg" {
  vpc_id = data.aws_vpc.default.id

  # Elasticsearch HTTP
  ingress {
    from_port = 9200
    to_port = 9200
    protocol = "tcp"
    cidr_blocks = [aws_subnet.subnet.cidr_block]
  }

  # Elasticsearch cluster communication
  ingress {
    from_port = 9300
    to_port = 9300
    protocol = "tcp"
    cidr_blocks = [aws_subnet.subnet.cidr_block]
  }

  # SSH
  ingress {
    from_port = 22
    to_port = 22
    protocol = "tcp"
    cidr_blocks = [
        "192.18.0.1/32"  # edit your external IP here!
    ]
  }

  # In a real deployment you should tighten this up, but for the sake of an example...
  egress {
    from_port = 0
    to_port = 0
    protocol = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}
```

And finally let's set up the actual instance:

```terraform
resource "aws_instance" "vm" {
  count = var.vm_count

  ami                         = data.aws_ami.ubuntu.id
  instance_type               = var.instance_type
  key_name                    = var.key_name
  subnet_id                   = aws_subnet.subnet.id
  vpc_security_group_ids      = [aws_security_group.sg.id]
  associate_public_ip_address = true
  availability_zone           = var.aws_az

  tags = {
    "LocalName" = "analysis-${count.index+1}"
  }

  root_block_device {
    volume_type = "gp2"
    volume_size = 120
    encrypted   = true
  }

  lifecycle {
    ignore_changes = [ami]
  }
}
```

So far this has been plain regular Terraform with the exception of the "magically" set `vpc_id` variable, but to have
Ansible work properly, we need some outputs. Let's set up some templates, first for an SSH configuration to make Ansible
faster with SSH multiplexing, and life for us generally easier by being able to refer to node names instead of public
IP addresses (`analysis-host/templates/ssh_config.tpl`):

```
Host *
  User ${system_user}
  StrictHostKeyChecking accept-new
  ForwardAgent yes
  ControlMaster auto
  ControlPath ~/.ssh/cm_socket/%r@%h:%p
  ControlPersist 15s

${vm_block}
```

Then, let's have a very simple template for an Ansible inventory file (`analysis-host/templates/hosts.tpl`):

```
[base]
${all_node_names}
```

Finally, let's have Terraform actually output something using the templates. Note that in this example, the Ansible
inventory file refers to node names as well, not public IP addresses - Dreamer will automatically use the `ssh_config`
output when running Ansible.

```terraform
output "ssh_config" {
  value = templatefile(
    "${path.root}/templates/ssh_config.tpl",
    {
      vm_block = join(
        "\n\n",
        formatlist(
          "Host %s\n    HostName %s",
          aws_instance.vm.*.tags.LocalName,
          aws_instance.vm.*.public_ip
        )
      )
      system_user = "ubuntu"
    }
  )
}

output "ansible_hosts" {
  value = templatefile(
    "${path.root}/templates/hosts.tpl",
    {
      all_node_names = join("\n", aws_instance.vm.*.tags.LocalName)
    }
  )
}
```

To wrap this all up, we need a Dreamer module: (`analysis-host/dream.py`):

```python
from dreamer import Module

class AnalysisHostModule(Module):
    friendly_name = 'analysis-host'
    depends_on = 'analysis-vpc'
```

All we need to do is to set up the dependency (`Module.depends_on`) and the friendly name of the module.

As for Ansible, there is nothing that should be considered in the playbook if you're running it with Dreamer: you are
responsible for generating both the inventory file and the playbook. By default the playbook should be in
`analysis-host/ansible/play.yml`, but this can be changed by overriding `Module.playbook_path` in your `dream.py`.

To deploy this long-winded example of a module, let's presume you did not destroy the `analysis-vpc` example from above
(if you did, simply re-deploy it):

```sh
$ dream analysis-host analyser --parent analysis-vpc/test
```

After giving Terraform the values of the variables, you should see an EC2 instance deployed and the Ansible playbook
applied to it.

At this point you might be wondering: how do you access the instance yourself? There are two options: either grab the
public IP from the Terraform outputs in your console, or run the following commands:

```sh
$ ssh $DREAMER_BASE_DIR/analysis-host/analyser/ssh_config analysis-1
```

(For centralised state in S3 buckets, Dreamer provides the default step `pull` which will download a local copy of the state for you to use.)

One of the major strengths of Dreamer is if you want to make changes to the running instance. Let's say you are still
developing the Ansible playbook, so you make some changes to it and want to apply the changes. To do that, simply run:

```sh
$ dream analysis-host analyser -o ansible
```

After you reach a satisfactory state with your Ansible playbook, it should be very easy to move from Dreamer to using
e.g. HashiCorp Packer to create AMIs for easier usage.
