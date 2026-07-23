from glob import glob
from setuptools import find_packages, setup
package_name = "mujoco_bridge_agibot_x2"
setup(name=package_name, version="0.1.0", packages=find_packages(), data_files=[
    ("share/ament_index/resource_index/packages", [f"resource/{package_name}"]),
    (f"share/{package_name}", ["package.xml"]), (f"share/{package_name}/launch", glob("launch/*.py")),
    (f"share/{package_name}/config", glob("config/*.yaml"))], install_requires=["setuptools", "mujoco>=3.1"],
    entry_points={"console_scripts": ["agibot_x2_bridge = x2.node:main"]})
