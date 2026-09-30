from setuptools import find_packages, setup

setup(
    name="nemd",
    version="0.1.0",
    description="Local search for project documentation",
    python_requires=">=3.9",
    package_dir={"": "src"},
    packages=find_packages("src"),
    extras_require={"semantic": ["torch", "transformers", "sentencepiece", "protobuf"]},
    entry_points={"console_scripts": ["nemd=nemd.cli:main"]},
)
