package handlers

import (
	"bytes"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/gin-gonic/gin"
	"go.uber.org/zap"
)

func TestPostAction_ValidJSON(t *testing.T) {
	gin.SetMode(gin.TestMode)
	router := gin.New()
	h := NewHandlers(zap.NewNop())
	router.POST("/action", h.PostAction)

	req := httptest.NewRequest(http.MethodPost, "/action", bytes.NewBufferString(`{"command":"start"}`))
	res := httptest.NewRecorder()

	router.ServeHTTP(res, req)

	if res.Code != http.StatusOK {
		t.Fatalf("expected status 200, got %d", res.Code)
	}
}

func TestPostAction_InvalidJSON(t *testing.T) {
	gin.SetMode(gin.TestMode)
	router := gin.New()
	h := NewHandlers(zap.NewNop())
	router.POST("/action", h.PostAction)

	req := httptest.NewRequest(http.MethodPost, "/action", bytes.NewBufferString(`{"command":`))
	res := httptest.NewRecorder()

	router.ServeHTTP(res, req)

	if res.Code != http.StatusBadRequest {
		t.Fatalf("expected status 400, got %d", res.Code)
	}
}
