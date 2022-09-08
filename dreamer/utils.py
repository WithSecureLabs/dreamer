#!/usr/bin/env python3

"""Small helper functions and constants for Dreamer."""

import difflib
import logging
import os
import pathlib
import shlex
import shutil
import subprocess
import sys
import tempfile

from typing import Callable, Optional, TextIO, Tuple, Union

# Helpers for command-line
RED = '\033[91m'
YELLOW = '\033[93m'
GREEN = '\033[92m'
MAGENTA = '\033[95m'
WHITE = '\033[97m'
RESET = '\033[0m'


class ColorFormatter(logging.Formatter):
    """
    A simple log formatter that colors output lines depending on their level.
    """
    def format(self, record: logging.LogRecord) -> str:
        if record.levelno >= logging.CRITICAL:
            color = MAGENTA
        elif record.levelno >= logging.ERROR:
            color = RED
        elif record.levelno >= logging.WARNING:
            color = YELLOW
        elif record.levelno >= logging.INFO:
            color = GREEN
        else:
            color = WHITE
        msg = super().format(record)
        return f'{color}{msg}{RESET}'


def git_branch() -> Optional[str]:
    """Return current git branch name or None if not in a git repository"""
    try:
        return subprocess.check_output(
            ['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
            stderr=subprocess.DEVNULL,
        ).strip(b'\n').decode('utf-8')
    except subprocess.CalledProcessError:
        return None


def git_version(path: pathlib.Path) -> str:
    """
    Return a human-readable "version" of the git repository status at the given path.

    The output is as follows:
    * If an exact tag match is available, use it as a base (e.g. "0.3.0")
    * Otherwise use the branch name followed by the commit abbrev as a base
    * If the repository is in a non-pristine state, "-dirty" is appended to the base
    * If none of this information could be found, "unknown" is returned
    """

    with subprocess.Popen(
            ['git', 'describe', '--tags', '--exact-match'],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            universal_newlines=True,
            cwd=path) as child:
        return_code = child.wait()
        if return_code != 0 or child.stdout is None:
            tag = None
        else:
            tag = child.stdout.read()[:-1]  # strip trailing newline

    with subprocess.Popen(
            ['git', 'symbolic-ref', '-q', '--short', 'HEAD'],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            universal_newlines=True,
            cwd=path) as child:
        return_code = child.wait()
        if return_code != 0 or child.stdout is None:
            branch = None
        else:
            branch = child.stdout.read()[:-1]

    with subprocess.Popen(
            ['git', 'rev-parse', '--short', 'HEAD'],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            universal_newlines=True,
            cwd=path) as child:
        return_code = child.wait()
        if return_code != 0 or child.stdout is None:
            commit = None
        else:
            commit = child.stdout.read()[:-1]

    with subprocess.Popen(
            ['git', 'status', '--porcelain'],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            universal_newlines=True,
            cwd=path) as child:
        return_code = child.wait()
        if return_code != 0 or child.stdout is None:
            is_dirty = False  # if `git status` errors, it's possible this isn't a git repository at all
        else:
            is_dirty = len(child.stdout.read().strip()) > 0

    version = "unknown"
    if tag is not None:
        version = tag
    else:
        if branch is not None:
            version = branch
        if commit is not None:
            version += f'-{commit}'

    if is_dirty:
        version += '-dirty'

    return version

def quote(path: Union[str, pathlib.Path]) -> str:
    """Return the path quoted for safe shell usage."""
    # shlex.quote() does not accept path-like objects. :(
    return shlex.quote(str(path))


def fail(msg: str) -> None:
    """Output the given error message in red and terminate the process with the exit code 1."""
    logging.critical(msg)
    sys.exit(1)


def run(cmd: str) -> None:
    """
    Run the given command, displaying it beforehand, and on a non-zero subprocess exit code, terminate the main process.
    """
    logger = logging.getLogger('dreamer.run')
    logger.info('Executing: %s', cmd)
    retval = subprocess.run(cmd, stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr, shell=True, check=True)
    if retval.returncode == 0:
        logger.info('OK: %s', cmd)
    else:
        logger.critical('Failed: %s', cmd)
        sys.exit(1)


def prompt(txt: str, terminate_on_no: bool = True) -> bool:
    """Prompt the user if they want to proceed, fail and terminate the process if not."""
    val = input(f'{YELLOW}{txt} [y/n] {RESET}').lower().strip()
    affirmative = val.startswith('y')
    if terminate_on_no and not affirmative:
        fail('Canceled by user')
    return affirmative


def merge_ssh_configs(source_path: pathlib.Path, destination_path: pathlib.Path, append: bool = True) -> None:
    """Merge two ssh_configs.

    Using this function allows one to safely use Include-directives in their
    SSH configuration files. Any Include-directive that contains the
    destination path will **not** be added during the merge.
    """
    def fn_append(_from: pathlib.Path, _to: pathlib.Path) -> None:
        with open(_to, 'a') as dst:
            with open(_from, 'r') as src:
                for lines in src.readlines():
                    dst.write(lines)

    def fn_prepend(_from: pathlib.Path, _to: pathlib.Path) -> None:
        with open(_to, 'r+') as dst:
            with open(_from, 'r') as src:
                dst_contents = dst.readlines()
                dst.seek(0)
                for lines in src.readlines() + dst_contents:
                    dst.write(lines)

    print(f"{GREEN}Merging SSH configurations: {source_path} -> {destination_path}{RESET}")
    expanded_dest = pathlib.Path(os.path.expanduser(destination_path))
    expanded_src = pathlib.Path(os.path.expanduser(source_path))
    with tempfile.NamedTemporaryFile(mode="w") as tmp:
        print(f"{GREEN}Created temporary file: {tmp.name}{RESET}")
        with open(expanded_src, "r") as src:
            lines = [_ for _ in src if _ not in (f"Include {expanded_dest}\n", f"Include {destination_path}\n")]
            tmp.writelines(lines)

        tmp.flush()
        os.fsync(tmp.fileno())
        if append:
            merge_files(pathlib.Path(tmp.name), expanded_dest, fn_append)
        else:
            merge_files(pathlib.Path(tmp.name), expanded_dest, fn_prepend)


def merge_files(source_path: pathlib.Path, destination_path: pathlib.Path,
                fn: Callable[[pathlib.Path, pathlib.Path], None]) -> None:
    """Merge two files based on their content:

    1) If destination file does not exist, source file is just copied there.
    2) If destination file does not have lines that exist in source file,
       source file is merged to destination file.
    3) If some source file lines exist in destination file, source file is
       merged to destination file, but the user is prompted to manually
       check the results.

    Merging is done using the function defined in the fn argument. It should not return anything.
    """
    src_len, _, matching_lines = compare(source_path, destination_path)

    if matching_lines == -1:
        shutil.copy(source_path, destination_path)
        print(f"{GREEN}Copied: {source_path} -> {destination_path}{RESET}")
    elif matching_lines == 0:
        fn(source_path, destination_path)
        print(f"{GREEN}Merged: {source_path} to {destination_path}{RESET}")
    elif matching_lines != src_len:
        fn(source_path, destination_path)
        print((f"{YELLOW}Merged: {source_path} to {destination_path}. "
               f"Please verify {destination_path} manually!{RESET}"))
    elif matching_lines == src_len:
        print((f"{YELLOW}File {destination_path} already contains {source_path}. "
               f"Please verify {destination_path} manually!{RESET}"))
    else:
        print(f"{RED}Matching lines: {matching_lines} not expected! STOP{RESET}")


def compare(this: pathlib.Path, that: pathlib.Path) -> Tuple[int, int, int]:
    """Compare two files and return (this_length, that_length, matching_lines)."""
    this_length = 0
    that_length = 0
    matching_lines = -1

    both_exist = this.exists() and that.exists()

    if this.exists():
        with open(this, 'r') as this_fp:
            this_lines = this_fp.readlines()
            this_length = len(this_lines)

    if that.exists():
        with open(that, 'r') as that_fp:
            that_lines = that_fp.readlines()
            that_length = len(that_lines)

    if both_exist:
        matcher = difflib.SequenceMatcher()
        matcher.set_seqs(this_lines, that_lines)
        matching_lines = sum((_.size for _ in matcher.get_matching_blocks()))

    return this_length, that_length, matching_lines


def replace_block(handle: TextIO, name: str, data: str) -> None:
    """
    Add of replace a block of content in the given I/O handle. Blocks are delimited with lines in the form
    "# start: blockname" and "# end: blockname".
    The file is always written with Unix newlines, but is read with universal newlines.
    The current stream position of the file handle will not be preserved.
    """

    # If reading all lines into memory somehow becomes a performance issue in the future, this method should obviously
    # be revamped to iterate the file instead.
    start_line = f'# start: {name}'
    new_content = data.splitlines()
    end_line = f'# end: {name}'

    handle.seek(0)
    content = [line.strip() for line in handle.readlines()]

    if start_line not in content:
        content.append(start_line)
        content += new_content
        content.append(end_line)
    else:
        start_idx = content.index(start_line)
        end_idx = content.index(end_line)
        content = content[:start_idx + 1] + new_content + content[end_idx:]

    handle.seek(0)
    handle.truncate()
    handle.write('\n'.join(content))
