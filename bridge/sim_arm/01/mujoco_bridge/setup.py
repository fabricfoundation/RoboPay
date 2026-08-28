from setuptools import setup, find_packages
from glob import glob

package_name = "mujoco_bridge_sim_arm_01"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", [f"resource/{package_name}"]),
        (f"share/{package_name}", ["package.xml"]),
        (f"share/{package_name}/launch", glob("launch/*.py")),
        (f"share/{package_name}/config", glob("config/*.yaml")),
        (f"share/{package_name}/profiles", glob("profiles/*.yaml")),
        (f"share/{package_name}/examples", glob("examples/*.jsonc")),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    entry_points={
        "console_scripts": [
            "sim_arm_01_bridge = sim_arm_01.node:main",
            "sim_arm_01_flow_demo = sim_arm_01.flow.demo:main",
            "sim_arm_01_sim_to_sim = sim_arm_01.sim_to_sim:main",
        ],
    },
)
