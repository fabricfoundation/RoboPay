from launch import LaunchDescription
from launch_ros.actions import Node

def generate_launch_description():
    return LaunchDescription([
        Node(package="isaac_sim_bridge_x30_pro", executable="bridge", output="screen")
    ])
