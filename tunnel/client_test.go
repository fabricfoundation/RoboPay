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

func TestRouteSpecificMiddleware(t *testing.T) {
	router := NewRouter()

	appendHeader := func(name, value string) Middleware {
		return func(next Handler) Handler {
			return func(method string, headers map[string]string, body []byte) (int, map[string]string, []byte) {
				status, respHeaders, respBody := next(method, headers, body)
				if respHeaders == nil {
					respHeaders = map[string]string{}
				}
				respHeaders[name] = value
				return status, respHeaders, respBody
			}
		}
	}

	router.Register("GET", "/with-middleware", func(method string, headers map[string]string, body []byte) (int, map[string]string, []byte) {
		return 200, map[string]string{"content-type": "text/plain"}, []byte("ok")
	}, appendHeader("x-route-middleware", "enabled"))

	router.Register("GET", "/without-middleware", func(method string, headers map[string]string, body []byte) (int, map[string]string, []byte) {
		return 200, map[string]string{"content-type": "text/plain"}, []byte("ok")
	})

	_, withHeaders, _, err := router.Handle("/with-middleware", "GET", nil, nil)
	if err != nil {
		t.Fatalf("expected no error for route with middleware, got %v", err)
	}
	if got := withHeaders["x-route-middleware"]; got != "enabled" {
		t.Fatalf("expected route-specific middleware header, got %q", got)
	}

	_, withoutHeaders, _, err := router.Handle("/without-middleware", "GET", nil, nil)
	if err != nil {
		t.Fatalf("expected no error for route without middleware, got %v", err)
	}
	if _, ok := withoutHeaders["x-route-middleware"]; ok {
		t.Fatal("expected middleware header to be absent on route without middleware")
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
