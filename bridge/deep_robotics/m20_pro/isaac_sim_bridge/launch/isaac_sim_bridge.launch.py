from launch import LaunchDescription
from launch_ros.actions import Node
from launch.substitutions import PathJoinSubstitution
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():
    config = PathJoinSubstitution(
        [FindPackageShare("isaac_sim_bridge_m20_pro"), "config", "default.yaml"]
    )
    return LaunchDescription([
        Node(
            package="isaac_sim_bridge_m20_pro",
            executable="isaac_sim_bridge",
            name="isaac_sim_bridge_m20_pro",
            output="screen",
            parameters=[config],
        )
    ])
