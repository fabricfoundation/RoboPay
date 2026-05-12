package handlers

import "github.com/fabricfoundation/robot-tunnel-client/internal"

// RobotID returns a handler that responds with the robot's ID.
func RobotID(robotID string) internal.Handler {
	return func(method string, path string, headers map[string]string, body []byte) (int, map[string]string, []byte) {
		return 200, map[string]string{"content-type": "text/plain"}, []byte(robotID)
	}
}
