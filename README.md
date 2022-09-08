# Dreamer: Terraform and Ansible made easier

**Dreamer** is a deployment tool designed for fast(er) prototyping of cloud infrastructure. It is based on running
Terraform and Ansible automatically, making it faster and easier to develop templates for cloud-hosted virtual machines,
especially those intended to be used interactively via a terminal or graphical user interface (as opposed to e.g. web
servers). It is also useful for creating virtual machines from a template when a pre-built template (e.g. AMI) is not
desirable.

Dreamer started its journey as a humble shell script for just running Terraform and Ansible together, but quickly
evolved into a more complex tool with some features including:

* Centralised state management (optionally in S3)
* A module system with support for dependencies
* Support for optional (post-)deployment steps

Dreamer is intended to be the beginning of the lifecycle of a cloud-hosted service. After having a release-ready
Ansible configuration, we recommend creating an image with e.g. [Packer](https://packer.io) and using that in the
later steps of the lifecycle, such as integration testing and production.

## Installation

At the moment, it's recommended to just clone Dreamer and run `pip install .`. If you feel like you will make changes
to Dreamer, use `pip install -e .` so any changes you make are instantly available in your (hopefully virtual)
environment.

**NB.** `boto3` is an **optional** dependency used when storing the project state in an S3 bucket and is not
installed by default. You will get a custom error message asking you to install it if you try to use remote state
without it.

## Using Dreamer

Dreamer stores the state of all projects ("dreams", if you wish) in a central location, which is set either by using
the `-b` argument to it or the `DREAMER_BASE_DIR` environment variable. While the latter is obviously more convenient,
the former is occasionally handy.

Setting up a new dream is as simple as navigating to a directory that contains a *subdirectory* containing a Dreamer
module. For example, if you have a Dreamer module in `~/code/my_cloud_project/infrastructure/dream.py`, you would run
Dreamer in `~/code/my_cloud_project/` with the `infrastructure` parameter:

    my_cloud_project$ dream infrastructure test_infra

The command would set up a cloud environment as specified in the `infrastructure` with the name `test_infra`. If you
had `DREAMER_BASE_DIR` set as `~/.dreamer_state/`, you would now have a directory
`~/.dreamer_state/infrastructure/test_infra/` containing various files depending on what the `infrastructure` module
contains.

*NB.* As there is currently no "namespacing" of module names, `infrastructure` is likely a too generic name.

## Full list of environment variables

* `DREAMER_BASE_DIR`: The state storage directory, corresponds to the `-b` argument.
* `DREAMER_MODULE_REPOSITORY`: The root directory containing your Dreamer module definitions, corresponds to the
  `-r` argument.
* `DREAMER_VAR_FILES`: A list of Terraform variable files to use for all Terraform runs, useful for setting default
  values for variables.
* `DREAMER_SSH_KEY`: The SSH key used by default to run Ansible. Otherwise defaults to whatever `ssh-agent` has.

## Using a remote Dreamer state

Dreamer can store its state in an Amazon S3 bucket. If the base dir given by `DREAMER_BASE_DIR` or `-b` starts with
`s3://`, remote state will be used.

Further technical details of the remote state management are to be written; for now, see the `AWSFileProvider` class
for details.

## Making a Dreamer module

A Dreamer module contains a Python class deriving from `dreamer.Module`. The base module is extensively documented and
should be easy to build on. There is an example module in the `example/` directory, and a walkthrough for creating one
is included in `docs/more_examples.md`.

### Required Terraform outputs

These Terraform outputs are required *by default* and are used by the *default* module steps. The defaults can always
be overridden by your module, but have been found good for building on.

#### ansible_hosts

This output should contain a valid Ansible inventory file containing the hosts created by Terraform.

#### ssh_config

This output should be a valid OpenSSH configuration file. At the bare minimum it does not require anything, but if the
built infrastructure uses a bastion host, the file should define the necessary directives so all SSH connections are
routed via the bastion host. In addition, setting `ControlMaster` and related variables is strongly suggested for
significantly faster Ansible usage.

Hint: Also consider setting a project-specific `UserKnownHostsFile`.

## Dependencies in Dreamer modules

A common use case for Dreamer is deploying an _environment_ that might consist of a network, a bastion host, maybe a
database host etc., and later deploy multiple installations of another module inside that environment. To do this,
Dreamer supports dependencies, where a module can easily refer to a parent module. Dreamer does this by creating a
standard Terraform variable file automatically in the `plan` step, currently named `export.tfvars`.

The variable file contains all Terraform variables used to create the deployment. The variables will have a prefix that
you should define in `Module.export_variable_prefix`. Additionally, you can include your own Terraform outputs in the
export file by adding their names to `Module.export_outputs`. The format of the outputs is not enforced by Dreamer, so
your should take care yourself to ensure that all Terraform outputs included in `export_outputs` are syntactically
valid. Nothing in the output is automatically prefixed either, unlike the automatically-included Terraform variables -
whether or not you prefix them is your choice, but it is recommended to use the same prefix for consistency.

In the submodule, you set `Module.depends_on` to include the parent module name. After this, all variables in the
parent module's exported variables file are available as regular Terraform variables for you to use.

When a module depends on a parent module, Dreamer requires you to use the `--parent` command-line argument with a
parameter in the form `module/project`. As a concrete example, if you have the modules `collab_env` and `collab` with
the latter depending on the former, you might run Dreamer as follows: `dream collab test --parent collab_env/test`.

For an example of this, see the IR Terraform `collab` and `collab_env` modules.

## Why Dreamer?

Dreamer might be the tool you need if you are:

* Manually running Ansible right after deploying resources with Terraform
* Using a lightweight shell script for integration between Terraform an Ansible
* Running Ansible in a Terraform provisioner
* Constantly entering network parameters manually to a deployment
* Manually entering or copy-pasting complex shell invocations after deploying resources with Terraform

## Why not Dreamer?

Dreamer is not a perfect tool for every situation – after all, it was initially purpose-built for a specific use case.
This means that it might not be suitable in the following situations:

* If you don't require interactive shell/GUI access to the deployed servers.
* In a similar vein, if you are using an orchestration service like Kubernetes, Dreamer is probably not for you.
* If you want to use a monolithic Ansible playbook setup with a single complex inventory, Dreamer might be difficult to
  integrate into it.
* If you just want to abstract the usage of Terraform and Ansible away from your users: despite our best efforts, your
  users will see many of Terraform's and Ansible's error messages as-is.

Depending on your exact use case, you might instead consider:

* For network services without interactive shell/GUI, consider containers and a CI/CD pipeline.
* For orchestrated container systems, your ecosystem probably has suitable development and debugging features available:
  e.g., Kubernetes telepresence, Minikube, and the fact that Dockerfiles roughly correspond to Ansible playbooks.
* For user-friendlier access to Terraform and Ansible, consider e.g. Terraform Cloud or AWX.
* For modular Terraform deployments without the need for Ansible, simply use Terraform's own module system.
