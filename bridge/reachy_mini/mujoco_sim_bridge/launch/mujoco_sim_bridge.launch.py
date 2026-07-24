from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription([
        Node(
            package="mujoco_sim_bridge_reachy_mini",
            executable="mujoco_sim_bridge",
            name="mujoco_sim_bridge_reachy_mini",
            output="screen",
        ),
    ])
