#!/usr/bin/env python3

"""The main CLI entrypoint for Dreamer."""

import argparse
import logging
import os
import os.path
import shlex
import subprocess
import sys

from itertools import chain
from pathlib import Path
from typing import List, Optional, Tuple, Union

from . import Module
from .exceptions import ProgrammerError
from .loader import load_modules
from .providers.aws import AWSFileProvider
from .providers.base import AbstractFileProvider
from .providers.local import LocalFileProvider
from .pseudo_modules import PseudoModule
from .runconfig import RunConfig, TerraformRunConfig
from .utils import ColorFormatter

AnyModule = Union[Module, PseudoModule]


def sanity_check(logger: logging.Logger) -> None:
    """
    Run sanity checks for Dreamer usage.
    """
    # Terraform always prefers a local "terraform.tfstate" file even if a state file is given on the command line,
    # bail out if one exists in the current directory
    if Path('terraform.tfstate').is_file():
        logger.error('Refusing to start due to a `terraform.tfstate` file being present in the current directory.')
        sys.exit(1)


def parse_arguments(logger: logging.Logger) -> Tuple[argparse.Namespace, List[str]]:
    """
    Parse Dreamer CLI arguments. Returns a tuple of (parsed_arguments, remaining_arguments).
    """

    # Parse arguments
    parser = argparse.ArgumentParser(
        description='Initialize a module with Terraform and Ansible.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('-o', '--operation', action='store', help='only run the given step(s), comma-separated')
    parser.add_argument('-d', '--base_dir', action='store', help='the base directory for all Dreamer projects')
    parser.add_argument('-v', '--var_files', action='append', default=[], help='Terraform variable files')
    parser.add_argument('-r', '--module-repository', action='append', type=Path, default=[],
                        help='include this Dreamer module repository path (defaults to cwd)')
    parser.add_argument('-m', '--module-file', action='append', type=Path, default=[],
                        help='include a specific module file')
    parser.add_argument('-i', '--ssh-key', action='store', help='use the SSH key specified with Ansible')
    parser.add_argument('-p', '--parent', action='append', default=[],
                        help='parent module references in the form module_name/project_name')
    parser.add_argument('--no-color', action='store_true', help='do not colorize output')
    parser.add_argument('--debug', action='store_true', help='show debug output')
    parser.add_argument('--ansible-args', action='store',
                        help='additional arguments for Ansible (mostly for development)')
    parser.add_argument('--terraform-args', action='store',
                        help='additional arguments for Terraform (mostly for development)')
    parser.add_argument('--no-agent', action='store_true', help='skip ssh-agent checks and warnings')
    parser.add_argument('module', action='store', help='the module to set up')
    parser.add_argument('name', action='store', nargs='?', default=None, help='the name of this project')
    args, remaining = parser.parse_known_args()

    # Load arguments from environment variables, too
    if os.environ.get('DREAMER_VAR_FILES'):
        args.var_files += shlex.split(os.environ['DREAMER_VAR_FILES'])

    if not args.ssh_key and os.environ.get('DREAMER_SSH_KEY'):
        args.ssh_key = os.environ['DREAMER_SSH_KEY']

    args.module_repository += [Path(p) for p in shlex.split(os.environ.get('DREAMER_MODULE_REPOSITORY', ''))]

    if not args.module_repository:
        args.module_repository = [Path('.')]

    if not args.base_dir:
        environ_base_dir = os.environ.get('DREAMER_BASE_DIR')
        if environ_base_dir is None:
            logger.error(
                'No base directory given - use the -d argument or set the DREAMER_BASE_DIR environment variable'
            )
            sys.exit(1)
        args.base_dir = environ_base_dir

    return args, remaining


def configure_logging(args: argparse.Namespace) -> None:
    """
    Set up logging, setting the correct log level and output colorization depending on the command-line arguments.
    """
    root = logging.getLogger(None)
    root.handlers[0].setLevel(logging.DEBUG if args.debug else logging.INFO)
    if not args.no_color:
        root.handlers[0].setFormatter(ColorFormatter(
            fmt='%(asctime)s [%(name)s %(levelname)s] %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        ))


def verify_ssh_agent(given_ssh_key: Optional[str], logger: logging.Logger) -> None:
    """
    Verify that the SSH agent is running and that it has modules loaded. Part of the CLI startup process.
    """
    if not os.environ.get('SSH_AUTH_SOCK'):
        logger.error(
            'Warning: No indication of a running ssh-agent was found. Running Dreamer and especially its Ansible '
            'steps might hang due to a password prompt that is never displayed to you, and proxied connections '
            'might not work at all!'
        )
        logger.error('Consider running `eval $(ssh-agent -s)` or the equivalent.')
        logger.error('If you really know what you are doing, run Dreamer with `--no-agent`')
        sys.exit(1)
    else:
        result = subprocess.run(['ssh-add', '-L'], capture_output=True, check=False)
        if result.returncode != 0:
            logger.warning('Warning: There are no identities loaded to ssh-agent.')
            ssh_add_params = f' "{given_ssh_key}"' if given_ssh_key else ''
            logger.warning(f'Consider running `ssh-add{ssh_add_params}` or the equivalent.')


def get_runconfigs(args: argparse.Namespace) -> Tuple[TerraformRunConfig, RunConfig]:
    """
    Generate the `RunConfig` instances for Terraform and Ansible based on the command-line arguments.
    Returns a tuple of (tf_config, ansible_config).
    """
    tf_config = TerraformRunConfig(
        arguments=[f'-var-file={shlex.quote(fn)}' for fn in args.var_files]
    )
    ssh_args = RunConfig(arguments=[f'-i {shlex.quote(args.ssh_key)}']) if args.ssh_key else RunConfig()
    ansible_config = RunConfig(environment={
        'ANSIBLE_SSH_RETRIES': '3',
        'ANSIBLE_SSH_ARGS': ssh_args
    })

    # Add the manually given additional arguments
    if args.terraform_args:
        tf_config = tf_config.with_arguments(args.terraform_args)

    if args.ansible_args:
        ansible_config = ansible_config.with_arguments(args.ansible_args)

    return tf_config, ansible_config


def prepare_module(module: AnyModule, args: argparse.Namespace, logger: logging.Logger) -> List[str]:
    """
    Prepare the loaded Dreamer module by e.g. loading its parent modules and determining which steps to run.
    """
    # Map parents that were given to us as arguments
    parents = {}
    for parent_ref in args.parent:
        parent_module, parent_name = parent_ref.split('/', 1)
        parents[parent_module] = parent_name

    # Parse and validate steps to be executed
    if not args.operation:
        run_steps = module.default_steps
    else:
        steps = args.operation.split(',')
        for step in steps:
            if step not in module.steps:
                allowed_steps = ', '.join(module.steps)
                logger.error('Unknown step: %s, choices: %s', step, allowed_steps)
                sys.exit(1)
        run_steps = steps

    # If we are executing any step that needs the parents to be defined, load them
    if any(step in module.steps_requiring_parents for step in run_steps):
        module.load_parents(parents)

    # Show what we're running if it's a real module (not a pseudo-module)
    if not isinstance(module, PseudoModule):
        readable_steps = ', '.join(run_steps)
        logger.info('Dreaming up %s, steps: %s', args.module, readable_steps)

    return run_steps


def main() -> None:
    """The main CLI entrypoint of Dreamer."""
    # Log everything for now, re-configure after arguments have been parsed
    logging.basicConfig(
        format='%(asctime)s [%(module)s %(levelname)s] %(message)s',
        level=logging.DEBUG,
    )
    logger = logging.getLogger('dreamer.cli')
    sanity_check(logger)

    # Parse arguments
    args, remaining = parse_arguments(logger)

    # Re-configure logging
    configure_logging(args)

    # Find and load Dreamer modules
    _loader, modules, pseudo_modules = load_modules(args.module_repository, args.module_file)

    # Warn if `ssh-agent` is not running or has no keys loaded
    if not args.no_agent:
        verify_ssh_agent(args.ssh_key, logger)

    # Find the module class, preferring modules to pseudo-modules
    module_cls = modules.get(args.module, pseudo_modules.get(args.module))
    if module_cls is None:
        # If no modules were found, show a different error message
        if not modules:
            logger.critical('No modules were found - maybe try using the `-r` or `-m` parameters?')
        module_names = ', '.join(chain(modules.keys(), pseudo_modules.keys()))
        logger.critical('Invalid module name: %s, available choices: %s', args.module, module_names)
        sys.exit(1)

    # Raise an error if the project name is not given for a real module (pseudo-modules do not need one)
    if issubclass(module_cls, Module):
        if not args.name:
            logger.critical('`name` is a required argument for module %s', args.module)
            sys.exit(1)

    # Generate the run configurations
    tf_config, ansible_config = get_runconfigs(args)

    # Instantiate the file provider
    file_provider: AbstractFileProvider
    if args.base_dir.startswith('s3://'):
        file_provider = AWSFileProvider(args.base_dir)
    else:
        file_provider = LocalFileProvider(args.base_dir)

    with file_provider:
        # If we're running a real module (not a pseudo-module), set the default module and project of the file provider
        if not issubclass(module_cls, PseudoModule):
            file_provider.open_project(args.module, args.name)

        # Instantiate the class
        try:
            module: AnyModule = module_cls(
                args.name, file_provider, tf_config, ansible_config, additional_args=remaining
            )
        except ProgrammerError as e:
            logger.critical('Programmer error when initializing module "%s": %s', args.name, e)
            logger.critical('Aborting.')
            sys.exit(1)

        run_steps = prepare_module(module, args, logger)

        for step in run_steps:
            try:
                getattr(module, module.steps[step])()
            except ProgrammerError as e:
                logger.critical('Programmer error in step "%s": %s', step, e)
                logger.critical('This means that the developer of the module has made a mistake.')
                logger.critical('The traceback follows:')
                raise
            except:
                logger.critical('Step "%s" failed, the traceback follows.', step)
                raise


if __name__ == '__main__':
    main()
