package main

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/gin-gonic/gin"
)

func TestRequestRateLimitReturns429(t *testing.T) {
	gin.SetMode(gin.TestMode)
	t.Setenv("ACTION_RATE_LIMIT_RPM", "2")

	router := gin.New()
	router.Use(requestRateLimit())
	router.GET("/action", func(c *gin.Context) {
		c.Status(http.StatusOK)
	})

	for requestNumber := 1; requestNumber <= 3; requestNumber++ {
		request := httptest.NewRequest(http.MethodGet, "/action", nil)
		request.RemoteAddr = "198.51.100.10:12345"
		response := httptest.NewRecorder()
		router.ServeHTTP(response, request)
		expected := http.StatusOK
		if requestNumber == 3 {
			expected = http.StatusTooManyRequests
		}
		if response.Code != expected {
			t.Fatalf("request %d: expected HTTP %d, got %d", requestNumber, expected, response.Code)
		}
	}
}

func TestParseAllowedSkills(t *testing.T) {
	known := map[string]struct{}{"navigate_obstacle_course": {}, "stop": {}}
	allowed := parseAllowedSkills(" navigate_obstacle_course, stop, INVALID-SKILL!, navigate_obstacle_course, ", known)

	if len(allowed) != 2 {
		t.Fatalf("expected two configured skills, got %d", len(allowed))
	}
	for _, skill := range []string{"navigate_obstacle_course", "stop"} {
		if _, ok := allowed[skill]; !ok {
			t.Errorf("expected %q to be allowed", skill)
		}
	}
}

func TestParseAllowedSkillsIsRobotAgnostic(t *testing.T) {
	allowed := parseAllowedSkills("look_at_apple", map[string]struct{}{"look_at_apple": {}})
	if _, ok := allowed["look_at_apple"]; !ok {
		t.Fatal("expected a robot profile skill to be registered without shared-code changes")
	}
}

func TestParseAllowedSkillsEmptyFailsClosed(t *testing.T) {
	if allowed := parseAllowedSkills(" , ", map[string]struct{}{"stop": {}}); len(allowed) != 0 {
		t.Fatalf("expected an empty allowlist, got %d skills", len(allowed))
	}
}

func TestAllowedSkillsFromUnsetEnvFailsClosed(t *testing.T) {
	if allowed := allowedSkillsFromEnv("", false, map[string]struct{}{"stop": {}}); allowed != nil {
		t.Fatalf("expected no allowlist when ALLOWED_ACTIONS is unset, got %d skills", len(allowed))
	}
}
