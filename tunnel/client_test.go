package tunnel

import (
	"testing"
	"time"
)

func TestDemoHandlers(t *testing.T) {
	router := NewRouter()
	RegisterDemoHandlers(router)

	status, headers, body, err := router.Handle("/ping", "GET", nil, nil)
	if err != nil {
		t.Fatalf("expected no error for /ping, got %v", err)
	}
	if status != 200 {
		t.Fatalf("expected status 200 for /ping, got %d", status)
	}
	if string(body) != "pong" {
		t.Fatalf("expected body pong for /ping, got %q", string(body))
	}
	if headers["content-type"] != "text/plain" {
		t.Fatalf("expected text/plain header for /ping, got %q", headers["content-type"])
	}

	payload := []byte("hello")
	status, headers, body, err = router.Handle("/echo", "POST", nil, payload)
	if err != nil {
		t.Fatalf("expected no error for /echo, got %v", err)
	}
	if status != 200 {
		t.Fatalf("expected status 200 for /echo, got %d", status)
	}
	if string(body) != "hello" {
		t.Fatalf("expected echoed body for /echo, got %q", string(body))
	}
	if headers["content-type"] != "application/octet-stream" {
		t.Fatalf("expected application/octet-stream header for /echo, got %q", headers["content-type"])
	}
}

func TestUnknownPath(t *testing.T) {
	router := NewRouter()
	status, _, _, err := router.Handle("/missing", "GET", nil, nil)
	if err == nil {
		t.Fatal("expected error for unknown route")
	}
	if status != 404 {
		t.Fatalf("expected 404 for unknown route, got %d", status)
	}
}

func TestNextBackoff(t *testing.T) {
	if got := nextBackoff(1 * time.Second); got != 2*time.Second {
		t.Fatalf("expected 2s, got %v", got)
	}
	if got := nextBackoff(16 * time.Second); got != 30*time.Second {
		t.Fatalf("expected cap at 30s, got %v", got)
	}
	if got := nextBackoff(30 * time.Second); got != 30*time.Second {
		t.Fatalf("expected cap to remain at 30s, got %v", got)
	}
}
