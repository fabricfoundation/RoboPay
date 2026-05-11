package handlers

import "robot-autonomous-payment/tunnel"

// RegisterAll registers all real handlers on the router.
func RegisterAll(router *tunnel.Router, robotID string) {
	router.Register("GET", "/id", RobotID(robotID))
}

// RobotID returns a handler that responds with the robot's ID.
func RobotID(robotID string) tunnel.Handler {
	return func(method string, headers map[string]string, body []byte) (int, map[string]string, []byte) {
		return 200, map[string]string{"content-type": "text/plain"}, []byte(robotID)
	}
}
