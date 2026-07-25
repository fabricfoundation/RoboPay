from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription([
        Node(
            package="mujoco_bridge_sim_arm_01",
            executable="sim_arm_01_bridge",
            output="screen",
        ),
    ])
