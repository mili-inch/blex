"""Expected errors reported by the blex command-line interface."""


class BlexError(Exception):
    """Base class for failures that should be presented without a traceback."""


class ProjectError(BlexError):
    """A project layout, metadata, naming, or path validation failure."""


class ConfigurationError(BlexError):
    """An invalid ``[tool.blex]`` configuration."""


class ManifestError(BlexError):
    """An invalid Blender extension manifest."""


class InitError(BlexError):
    """A handled failure while initializing a project."""
