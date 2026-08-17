from setuptools import setup, find_packages

package_name = "zenoh_bridge"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", [f"resource/{package_name}"]),
        (f"share/{package_name}", ["package.xml"]),
    ],
    install_requires=["setuptools", "eclipse-zenoh"],
    zip_safe=True,
    entry_points={"console_scripts": []},
)
