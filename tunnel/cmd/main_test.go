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
