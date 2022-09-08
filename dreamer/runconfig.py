#!/usr/bin/env python3

"""Contains the RunConfig class and related utilities."""

from typing import List, Mapping, Optional, TypeVar, Union
from .utils import quote

AnyRunConfig = TypeVar('AnyRunConfig', bound='RunConfig')

class RunConfig:
    """
    Contains environment variables and arguments needed to run shell commands. Environment variable values can be
    RunConfigs on their own right, in which case only their arguments as produced by get_arguments() are used as the
    value of the environment variable.
    """

    def __init__(self, arguments: Optional[List[str]] = None,
                 environment: Optional[Mapping[str, Union[str, 'RunConfig']]] = None) -> None:
        self._arguments = arguments or []
        self._environment = environment or {}

    def with_arguments(self: AnyRunConfig, *arguments: str) -> AnyRunConfig:
        """Return a RunConfig with the given arguments added."""
        return RunConfig(self._arguments + list(arguments), self._environment)

    def with_environment(self: AnyRunConfig, name: str, value: Union[str, 'RunConfig']) -> AnyRunConfig:
        """Return a new RunConfig with the given environment variables added."""
        return RunConfig(self._arguments, {**self._environment, name: value})

    def with_child_arguments(self: AnyRunConfig, name: str, *arguments: str) -> AnyRunConfig:
        """Return a new RunConfig with the arguments of the given child RunConfig environment variable updated."""
        if name not in self._environment:
            raise KeyError(f'environment variable {name} not set in RunConfig')
        if not isinstance(self._environment[name], RunConfig):
            raise ValueError(f'environment variable {name} is not a RunConfig')
        new_child = self._environment[name].with_arguments(*arguments)  # type: ignore
        return RunConfig(self._arguments, {**self._environment, name: new_child})

    def get_arguments(self) -> str:
        """Return the arguments, joined by spaces."""
        return ' '.join(self._arguments)

    def get_environment(self) -> str:
        """Return the environment variables in the form 'A="a" B="b c"'"""
        def to_str(child: Union[str, RunConfig]) -> str:
            if isinstance(child, RunConfig):
                return child.get_arguments()
            return child
        return ' '.join(f'{name}={quote(to_str(value))}' for name, value in self._environment.items())

    def get_cmdline(self, command: str, suffix: Optional[str] = None) -> str:
        """
        Return a full command line for running the given command with the run configuration. The given command and
        suffix are not parsed in any way, so they can contain spaces (e.g. "terraform plan" as the command) or shell
        redirections (e.g. "> output_file.txt" as suffix).
        The output is in the format 'A="a" B="b c" command -arg1 -arg2 suffix'
        """
        prefix = self.get_environment()
        leader = ''
        if prefix:
            leader = f'{prefix} '
        trailer = ''
        if suffix:
            trailer = f' {suffix}'
        return f'{leader}{command} {self.get_arguments()}{trailer}'

    def __or__(self: AnyRunConfig, other: AnyRunConfig) -> AnyRunConfig:
        """
        Return the union of the two run configurations. The `other` run configuration takes precedence, overwriting
        the environment variables of this one.
        """
        if not isinstance(other, RunConfig):
            return TypeError(f'unsupported operand types for |: RunConfig and {type(other)}')
        # pylint: disable=W0212
        return RunConfig(
            self._arguments + other._arguments,
            {**self._environment, **other._environment}
        )
        # pylint: enable=W0212


class TerraformRunConfig:
    """
    A run configuration specific for Terraform, because Terraform >=0.15 requires very precise argument placement,
    to the absolute joy of automation developers across the world.

    The command line formed by this runconfiguration is in the following form:
    `[environment] <command> [global_arguments] <subcommand> [arguments] [suffix]`
    where `<command>` is probably usually `terraform`.
    """

    def __init__(self,
                 global_arguments: Optional[List[str]] = None,
                 arguments: Optional[List[str]] = None,
                 environment: Optional[Mapping[str, Union[str, AnyRunConfig]]] = None) -> None:
        self._global_arguments = global_arguments or []
        self._arguments = arguments or []
        self._environment = environment or {}

    def with_global_arguments(self, *arguments: str) -> 'TerraformRunConfig':
        """Return a TerraformRunConfig with the given global arguments added."""
        return TerraformRunConfig(
            self._global_arguments + list(arguments),
            self._arguments,
            self._environment
        )

    def with_arguments(self, *arguments: str) -> 'TerraformRunConfig':
        """Return a RunConfig with the given arguments added."""
        return TerraformRunConfig(
            self._global_arguments,
            self._arguments + list(arguments),
            self._environment
        )

    def with_environment(self, name: str, value: Union[str, AnyRunConfig]) -> 'TerraformRunConfig':
        """Return a new RunConfig with the given environment variables added."""
        return TerraformRunConfig(
            self._global_arguments,
            self._arguments,
            {**self._environment, name: value}
        )

    def with_child_arguments(self, name: str, *arguments: str) -> 'TerraformRunConfig':
        """Return a new RunConfig with the arguments of the given child RunConfig environment variable updated."""
        if name not in self._environment:
            raise KeyError(f'environment variable {name} not set in RunConfig')
        if not isinstance(self._environment[name], RunConfig):
            raise ValueError(f'environment variable {name} is not a RunConfig')
        new_child = self._environment[name].with_arguments(*arguments)  # type: ignore
        return TerraformRunConfig(self._arguments, {**self._environment, name: new_child})

    def get_global_arguments(self) -> str:
        """Return the global arguments, joined by spaces."""
        return ' '.join(self._global_arguments)

    def get_arguments(self) -> str:
        """Return the arguments, joined by spaces."""
        return ' '.join(self._arguments)

    def get_environment(self) -> str:
        """Return the environment variables in the form 'A="a" B="b c"'"""
        def to_str(child: Union[str, AnyRunConfig]) -> str:
            if isinstance(child, RunConfig):
                return child.get_arguments()
            return child
        return ' '.join(f'{name}={quote(to_str(value))}' for name, value in self._environment.items())

    def get_cmdline(self, command: str, tf_command: str, suffix: Optional[str] = None) -> str:
        """
        Return a full command line for running the given command with the run configuration. The given command and
        suffix are not parsed in any way, so they can contain spaces (e.g. "terraform plan" as the command) or shell
        redirections (e.g. "> output_file.txt" as suffix).
        The output is in the format 'A="a" B="b c" command -arg1 -arg2 suffix'
        """
        prefix = self.get_environment()
        leader = ''
        if prefix:
            leader = f'{prefix} '
        trailer = ''
        if suffix:
            trailer = f' {suffix}'
        return f'{leader}{command} {self.get_global_arguments()} {tf_command} {self.get_arguments()}{trailer}'

    def __or__(self, other: RunConfig) -> 'TerraformRunConfig':
        """
        Return the union of the two run configurations. The `other` run configuration takes precedence, overwriting
        the environment variables of this one; the arguments of `other` come after this one's.
        """
        if not isinstance(other, RunConfig):
            return TypeError(f'unsupported operand types for |: RunConfig and {type(other)}')
        if isinstance(other, TerraformRunConfig):
            return TerraformRunConfig(
                self._global_arguments + other._global_arguments,
                self._arguments + other._arguments,
                {**self._environment, **other._environment}
            )
        return TerraformRunConfig(
            self._global_arguments,
            self._arguments + other._arguments,
            {**self._environment, **other._environment}
        )
