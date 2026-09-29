from setuptools import find_packages, setup

setup(
    name="urag-fetch",
    version="0.1.0",
    description="Local search for project documentation",
    python_requires=">=3.9",
    package_dir={"": "src"},
    packages=find_packages("src"),
    entry_points={"console_scripts": ["urag=urag.cli:main"]},
)
