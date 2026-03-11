from setuptools import setup, find_packages

setup(
    name="nicocast",
    version="1.0.0",
    description="Miracast-compatible sink for Raspberry Pi Zero 2W",
    packages=find_packages(),
    include_package_data=True,
    package_data={"nicocast": ["templates/*.html"]},
    python_requires=">=3.10",
    install_requires=[
        "flask>=3.0,<4",
    ],
    entry_points={
        "console_scripts": [
            "nicocast=nicocast.main:main",
        ],
    },
)
