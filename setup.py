# Present so that `pip install -e .` has something to invoke; everything it
# would say lives in setup.cfg, including why the metadata is not in a
# pyproject.toml like it would be anywhere else.
from setuptools import setup

setup()
