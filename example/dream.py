#!/usr/bin/env python3

"""Contains a trivial example module for Dreamer."""

from dreamer import Module

class ExampleModule(Module):
    """A trivial example module for Dreamer. Launches a single EC2 instance."""
    friendly_name = 'example'
    playbook_path = 'playbook.yml'
