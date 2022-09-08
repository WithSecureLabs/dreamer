#!/usr/bin/env python3

"""The core of Dreamer, containing the base Module class."""

import argparse
import json
import logging
import pathlib
import shutil
import subprocess
import sys

from typing import Any, Dict, List, Mapping, Optional, Tuple, Union

from .exceptions import DreamerException, ProgrammerError
from .providers.base import AbstractFileProvider
from .runconfig import RunConfig, TerraformRunConfig
from .utils import git_branch, prompt, quote, replace_block, run


VARIABLE_EXPORT_FN = 'export.tfvars'
"""The standard filename for exported Terraform variables (variable names optionally prefixed)."""

VARIABLE_INTERNAL_FN = 'variables.tfvars'
"""The standard filename for the internally used Terraform variable cache (variable names always without a prefix)."""


class Module:
    """
    The base module for all Dreamer modules, providing reasonable defaults for a Terraform and Ansible project.
    """

    friendly_name = 'module'
    """The friendly name of this module, used on the command line and directory names"""

    steps: Mapping[str, str] = {
        'plan': 'plan',
        'apply': 'apply',
        'output': 'output',
        'ansible': 'ansible',
        'destroy': 'destroy',
        'cleanup': 'cleanup',
        'pull': 'pull'
    }
    """All runnable steps as a dict of <friendly name> -> <method name>"""

    default_steps: List[str] = ['plan', 'apply', 'output', 'ansible']
    """The default steps to run when no `-o` parameter is given"""

    steps_requiring_parents: List[str] = ['plan']
    """The steps which require the possible parent modules to be loaded."""

    outputs: Mapping[str, str] = {
        'ssh_config': 'ssh_config',
        'ansible_hosts': 'hosts',
    }
    """
    A dict of <Terraform output name> -> <filename>. Outputs are stored in the Dreamer state directory using the given
    filename without any added suffix, typically located in `$DREAMER_BASE_DIR/module/project/`. The two outputs
    defined here are required for the default steps, but can be removed in a child class if necessary.
    """

    ansible_args: str = ''
    """Additional arguments for Ansible"""

    playbook_path: str = 'ansible/play.yml'
    """The (relative) path to the Ansible playbook to run"""

    requirements_path: str = 'ansible/requirements.yml'
    """
    The (relative) path to the Ansible Galaxy requirements file. If the file exists, the requirements are imported
    when Ansible is run.
    """

    export_variable_prefix: str = ''
    """
    The prefix to use when generating the Terraform variable output for sub-dreams. As a concrete example, if you have
    a module with the variable `vpc_id` and set this prefix to `env_`, you would use the variable as `env_vpc_id` in all
    modules that depend on it.
    """

    export_outputs: Union[str, Tuple[str, ...]] = ()
    """
    The Terraform outputs to automatically include in the Terraform variable file exported for sub-modules. These
    outputs should only contain valid Terraform variable definitions, but this is NOT checked at all. The exported
    outputs are not automatically prefixed, unlike the exported variables.
    """

    terraform_vars: Dict[str, str] = {}
    """
    A dummy fallback for `self.terraform_vars` in the superclass initialization. It is usually more useful to set this
    as an instance variable in the subclass.
    """

    depends_on: Union[str, Tuple[str, ...]] = ()
    """
    The list of modules the current module depends on.
    """

    parser: Optional[argparse.ArgumentParser] = None
    """An optional ArgumentParser or subclass instance used to parse unparsed command-line arguments."""

    def __init__(self, name: str, file_provider: AbstractFileProvider, tf_config: TerraformRunConfig,
                 ansible_config: RunConfig, additional_args: Optional[List[str]]) -> None:
        """
        Initialize the class. When subclassing Module, please call this method in the subclass constructor only *after*
        settings some instance variables, as this constructor may parse pre-defined instance variables into a more
        easily usable form.
        """
        # pylint: disable=unused-argument
        self.logger = logging.getLogger(self.friendly_name)

        # Store parameters
        self.project_name = name
        self.provider = file_provider
        self.tf_config = tf_config
        self._ansible_config = ansible_config  # see Module.ansible_config()

        # Ensure that no output collides with the auto-generated filenames
        for output, filename in self.outputs.items():
            if filename in (VARIABLE_EXPORT_FN, VARIABLE_INTERNAL_FN):
                raise ProgrammerError(f'Forbidden filename specified for output "{output}": {filename}')

        # Parse the absolute path to the module and the Ansible files
        self.module_path = pathlib.Path(sys.modules[self.__module__].__file__).parent.resolve()
        self.playbook_path_abs = (self.module_path / self.playbook_path).resolve()
        self.requirements_path_abs = (self.module_path / self.requirements_path).resolve()

        # Terraform plan and state filename, and a list of all output files
        self.plan_fn = f'{self.friendly_name}.tfplan'
        self.state_fn = f'{self.friendly_name}.tfstate'
        self.state_backup_fn = f'{self.state_fn}.backup'
        self.files = list(self.outputs.values()) + [self.plan_fn, self.state_fn, self.state_backup_fn]

        # Add the current Git branch as a Terraform variable
        current_branch = git_branch()
        self.terraform_vars['git_branch'] = current_branch if current_branch is not None else ""

        # Update the Terraform run configuration with the Terraform variables
        for variable_name, variable_value in self.terraform_vars.items():
            self.tf_config = self.tf_config.with_environment(f'TF_VAR_{variable_name}', variable_value)

        # Update the Terraform run configuration to include the `-chdir` argument pointing at our module path
        self._tf_chdir_param = f'-chdir="{quote(self.module_path)}"'
        self.tf_config = self.tf_config.with_global_arguments(self._tf_chdir_param)

        # State caches
        self._tfstate: Mapping[str, Any] = {}
        self._requirements_installed = False

    @property
    def ansible_config(self) -> RunConfig:
        """
        Return the Ansible run configuration with the Ansible inventory file and SSH config file added as parameters.
        Done as a property because when the module is initialized, the inventory file might not yet exist, so
        FileProvider.get() would raise an exception.
        """
        return self._ansible_config.with_arguments(
            f'-i {quote(self.get_output("ansible_hosts"))}'
        ).with_child_arguments(
            'ANSIBLE_SSH_ARGS', f'-F {quote(self.get_output("ssh_config"))}'
        )

    def tf_config_with_state(self, *, writable: bool = False) -> RunConfig:
        """
        Convenience property for getting the Terraform run configuration with the state file.
        """
        if writable:
            fn = self.provider.get_rw(self.state_fn)
        else:
            fn = self.provider.get(self.state_fn)
        return self.tf_config.with_arguments(f'-state={quote(fn)}')

    def get_output(self, name: str, *, writable: bool = False) -> pathlib.Path:
        """A convenience method for getting the path of a Terraform output."""
        if writable:
            return self.provider.get_rw(self.outputs[name])
        return self.provider.get(self.outputs[name])

    def get_state(self, *path: str) -> Any:
        """
        Get a value from the Terraform state by a "path" passed as arguments, e.g.
        `self.get_state('modules[0]', 'resources', 'aws_instance.vm', 'primary', 'attributes', 'public_ip')` - yes, this
        is verbose, but that is how they are stored in the Terraform state.
        Does not catch exceptions, so be prepared to catch KeyErrors.
        """
        if not self._tfstate:
            with open(self.provider.get(self.state_fn), 'r') as file_:
                self._tfstate = json.load(file_)
        current = self._tfstate
        for piece in path:
            if piece.endswith(']'):
                idx_pos = piece.rfind('[')
                piece, item = piece[:idx_pos], piece[idx_pos + 1 : -1]
                current = current[piece][int(item)]
            else:
                current = current[piece]
        return current

    def load_parents(self, parents: Dict[str, str]) -> None:
        """
        Load the Terraform variables exported by parent modules, and add them to the current Terraform RunConfig.
        `parents` should be a dict of `{module_name: project_name}`
        """
        if isinstance(self.depends_on, str):
            expected_parents = {self.depends_on}
        elif isinstance(self.depends_on, tuple):
            expected_parents = set(self.depends_on)
        else:
            found = type(self.depends_on).__name__
            raise ProgrammerError(f'Module.depends_on should be either a tuple or a str, not {found}')

        given_parents = set(parents.keys())
        missing_parents = expected_parents - given_parents
        if missing_parents:
            raise DreamerException(f'Missing parents: {", ".join(missing_parents)} - use --parent to define them')
        unexpected_parents = given_parents - expected_parents
        if unexpected_parents:
            raise DreamerException(f'Unexpected parent given on command line: {", ".join(unexpected_parents)}')

        for module, name in parents.items():
            path = self.provider.get(VARIABLE_EXPORT_FN, module=module, project=name)
            self.tf_config = self.tf_config.with_arguments(f'-var-file={quote(path)}')

    def pull(self) -> None:
        """
        Pull the state to a directory called `state` in the current directory. Note that the state is not automatically
        updated, but is a static copy of the current state.
        """
        files = self.provider.get_files(self.project_name, self.friendly_name)
        output = pathlib.Path(f'./state-{self.friendly_name}-{self.project_name}')
        if not output.exists():
            self.logger.info('Created: %s', output)
            output.mkdir()
        for fn in files:
            file_ = self.provider.get(fn)
            shutil.copy(file_, output)
            self.logger.info('Copied: %s -> %s', fn, output / file_.name)

    def write_variables(self, plan_path: pathlib.Path) -> None:
        """
        Write all Terraform variables to an output file with a standard name (see `Dreamer.base.VARIABLE_EXPORT_FN`).
        This allows the variables to be inherited by sub-dreams in an easier way. Additionally write all Terraform
        variables without a prefix to a standard filename (`Dreamer.base.VARIABLE_INTERNAL_FN`), used in e.g.
        `Module.destroy`.
        """
        cfg = TerraformRunConfig(
            global_arguments=[self._tf_chdir_param],
            arguments=['-json', quote(plan_path)],
        )

        # Purposefully do not catch exceptions
        output = subprocess.check_output(cfg.get_cmdline('terraform', 'show'), shell=True)
        variables = json.loads(output.strip())['variables']

        # Create the output
        export_output = ''
        internal_output = ''
        for k, v in variables.items():
            value = v['value']  # Terraform escapes quotes for us, at least currently
            export_output += f'{self.export_variable_prefix}{k} = "{value}"\n'
            internal_output += f'{k} = "{value}"\n'

        export_fn = self.provider.get_rw(VARIABLE_EXPORT_FN)
        internal_fn = self.provider.get_rw(VARIABLE_INTERNAL_FN)
        with open(export_fn, 'r+') as export_fh:
            replace_block(export_fh, 'export', export_output)
        with open(internal_fn, 'w') as internal_fh:
            internal_fh.write(internal_output)

    def plan(self) -> None:
        """Run the planning step of Terraform, producing the `name.tfplan` file in the state directory."""
        plan_path = self.provider.get_rw(self.plan_fn)
        cfg = self.tf_config_with_state(writable=True).with_arguments(
            f'-out={quote(plan_path)}'
        )
        run(cfg.get_cmdline('terraform', 'plan'))
        self.write_variables(plan_path)

    def apply(self) -> None:
        """Run the apply step of Terraform, actually setting up cloud infrastructure."""
        # Apply does not use or even *accept* any other relevant parameters than the plan and the path to output the
        # state to, so use a bare run configuration here.
        cfg = TerraformRunConfig(
            global_arguments=[self._tf_chdir_param],
            arguments=[
                f'-state-out={quote(self.provider.get_rw(self.state_fn))}',
                quote(self.provider.get(self.plan_fn)),
            ],
        )
        run(cfg.get_cmdline('terraform', 'apply'))

    def output(self) -> None:
        """Generate and store all outputs of Terraform given in `self.outputs`."""
        # As with apply, output does not accept any superfluous arguments, but wants the regular state argument as
        # opposed to apply's state-out argument. So much for the DRY principle.
        cfg = TerraformRunConfig(
            global_arguments=[self._tf_chdir_param],
            arguments=[
                f'-state={quote(self.provider.get(self.state_fn))}',
                '-no-color',
                '-json',
            ]
        )

        export_outputs: Tuple[str, ...]
        if isinstance(self.export_outputs, tuple):
            export_outputs = self.export_outputs
        else:
            export_outputs = (self.export_outputs,)

        for output_name, fn in self.outputs.items():
            self.logger.info('Writing output %s to %s', output_name, fn)
            output_path = self.provider.get_rw(fn)
            output = subprocess.check_output(
                cfg.with_arguments(quote(output_name)).get_cmdline('terraform', 'output'),
                shell=True
            )
            parsed = json.loads(output.strip())
            with open(output_path, 'w') as f:
                f.write(str(parsed))

            if output_name in export_outputs:
                export_path = self.provider.get_rw(VARIABLE_EXPORT_FN)
                with open(export_path, 'r+') as export_fh:
                    content = open(output_path, 'r').read()
                    replace_block(export_fh, f'output: {output_name}', content)
        self.provider.sync()

    def ansible(self) -> None:
        """
        Run the Ansible playbook given as `self.playbook_path`, using the Terraform output `ansible_hosts` as the
        inventory file.
        """
        if self.requirements_path_abs.exists() and not self._requirements_installed:
            self.logger.info('Installing Ansible requirements')
            # --force for the win
            run(f'ansible-galaxy install --force --ignore-errors -r {self.requirements_path_abs}')
            self._requirements_installed = True
        run(self.ansible_config.get_cmdline('ansible-playbook', quote(self.playbook_path_abs)))

    def destroy(self) -> None:
        """
        Run the destroy step of Terraform, tearing down the infrastructure. Depends on the automatically generated
        output (specified by `Dreamer.VARIABLE_INTERNAL_FN`), just so Terraform does not needlessly ask for them again.
        """
        try:
            cfg = self.tf_config_with_state(writable=True).with_arguments(
                f'-var-file={self.provider.get(VARIABLE_INTERNAL_FN)}',
            )
        except FileNotFoundError:
            self.logger.warning('Terraform variable cache not found, trying without')
            cfg = self.tf_config_with_state(writable=True)
        run(cfg.get_cmdline('terraform', 'destroy'))

    def cleanup(self) -> None:
        """
        Clean up the Dreamer state files for the project, removing them and the project directory from the filesystem
        completely. Displays a list of all files going to be deleted and prompts the user before proceeding.
        """
        files = self.provider.get_files(self.project_name, self.friendly_name)
        if self.provider.project_exists(self.project_name):
            dirs = [self.provider.url_for_project(self.project_name, self.friendly_name)]
        else:
            dirs = []
        if not files and not dirs:
            self.logger.info('Nothing to clean up')
            return
        self.logger.warning('Files that will be deleted:')
        for file_ in files:
            self.logger.warning(' - %s', file_)
        self.logger.warning('Directories that will be deleted:')
        for dir_ in dirs:
            self.logger.warning(' - %s', dir_)
        prompt('Are you sure you want to clean up the Dreamer state?')
        self.provider.delete_project(self.project_name, self.friendly_name, recursive=True)
        self.logger.info('Cleaned up the state')
