from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from ament_index_python.packages import get_package_share_directory
from launch_ros.actions import Node
def generate_launch_description():
    share = get_package_share_directory("mujoco_bridge_agibot_x2")
    default_model = PathJoinSubstitution([share, "models", "x2.xml"])
    return LaunchDescription([
        DeclareLaunchArgument("model_path", default_value=default_model),
        DeclareLaunchArgument("robot_id", default_value="agibot-x2-sim-001"),
        Node(package="mujoco_bridge_agibot_x2", executable="agibot_x2_bridge", output="screen",
             parameters=[PathJoinSubstitution([share, "config", "params.yaml"]),
                         {"model_path": LaunchConfiguration("model_path"),
                          "robot_id": LaunchConfiguration("robot_id")}])
    ])
