"""Enables `python -m ZeiadYazajiGPT` (used internally by the Windows desktop
shortcut via pythonw.exe -m ZeiadYazajiGPT, so it launches with no console
window flash - see cli.py's _create_shortcut_windows). Equivalent to
running the `ZeiadYazajiGPT` console-script entry point directly.
"""

from .cli import main

if __name__ == "__main__":
    main()
