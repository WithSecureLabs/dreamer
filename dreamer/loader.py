"""This module contains the internal machinery for loading Dreamer modules."""

import importlib.machinery
import logging
import os.path
import sys

from pathlib import Path
from typing import Dict, List, Tuple, Type
from types import ModuleType

from . import Module
from .pseudo_modules import PseudoModule

class ModuleLoader:
    """
    A convenience class for importing modules by path. In our case, just importing the module and keeping a reference
    to it is enough to populate Module.__subclasses__(). Note that a reference *must* be kept, because subclass
    references are weak and thus do not actually keep the subclass loaded by themselves.
    """

    def __init__(self) -> None:
        self.modules: List[ModuleType] = []

    def load_module(self, module_path: Path) -> None:
        """Load a module, storing it in self.modules"""
        module_name = module_path.parent.name + '.dream'
        loader = importlib.machinery.SourceFileLoader(module_name, str(module_path))
        module = ModuleType(loader.name)
        loader.exec_module(module)
        sys.modules[module_name] = module
        module.__file__ = str(os.path.abspath(module_path))
        self.modules.append(module)


def load_modules(base_paths: List[Path],
                 module_files: List[Path]) -> Tuple[ModuleLoader, Dict[str, Type[Module]], Dict[str, Type[PseudoModule]]]:
    """
    Load all modules from the given directory and its immediate subdirectories. The `ModuleLoader` instance returned
    by this function must not be garbage collected to keep references to the actual modules around long enough.
    Returns a tuple of (loader, module_map, pseudomodule_map).
    """
    logger = logging.getLogger('dreamer.cli.load_modules')
    loader = ModuleLoader()

    for path in base_paths:
        path = path.expanduser()
        logger.debug('Finding modules in %s', path)
        module = path / 'dream.py'
        if module.exists():
            loader.load_module(module)
        for child_path in path.glob('*/dream.py'):
            loader.load_module(child_path)

    if module_files:
        for fn in module_files:
            loader.load_module(fn)

    modules = {m.friendly_name: m for m in Module.__subclasses__()}
    pseudo_modules = {pm.friendly_name: pm for pm in PseudoModule.__subclasses__()}

    return loader, modules, pseudo_modules
