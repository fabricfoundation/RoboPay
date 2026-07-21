from setuptools import setup

package_name = "isaac_sim_bridge_x30_pro"

setup(
    name=package_name,
    version="0.1.0",
    packages=[package_name.replace("-", "_"), "x30_pro"],
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        ("share/" + package_name + "/config", ["config/default.yaml"]),
        ("share/" + package_name + "/launch", ["launch/isaac_sim_bridge.launch.py"]),
    ],
    install_requires=["setuptools"],
    entry_points={"console_scripts": ["bridge = x30_pro.node:main"]},
)
