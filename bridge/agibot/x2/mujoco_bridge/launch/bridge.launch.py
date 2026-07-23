from launch import LaunchDescription
from launch_ros.actions import Node
def generate_launch_description():
    return LaunchDescription([Node(package="mujoco_bridge_agibot_x2", executable="agibot_x2_bridge", output="screen")])
