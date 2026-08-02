from launch import LaunchDescription
from launch_ros.actions import Node
from launch.substitutions import PathJoinSubstitution
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():
    config = PathJoinSubstitution(
        [FindPackageShare("sim_bridge_reachy_mini"), "config", "default.yaml"]
    )
    return LaunchDescription([
        Node(
            package="sim_bridge_reachy_mini",
            executable="sim_bridge",
            name="sim_bridge_reachy_mini",
            output="screen",
            parameters=[config],
        )
    ])
