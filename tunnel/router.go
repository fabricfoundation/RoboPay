package tunnel

import "fmt"

// Handler processes an incoming HTTP-request envelope and returns the response parts.
type Handler func(method string, headers map[string]string, body []byte) (status int, respHeaders map[string]string, respBody []byte)

// Router is a simple path-based handler registry.
type Router struct {
	handlers map[string]Handler
}

// NewRouter creates a new empty Router.
func NewRouter() *Router {
	return &Router{handlers: make(map[string]Handler)}
}

// Register adds a handler for the given method + path combination.
func (r *Router) Register(method string, path string, handler Handler) {
	key := method + " " + path
	r.handlers[key] = handler
}

// Handle dispatches to the registered handler for method + path. Returns 405 if the
// path exists under a different method, or 404 if no route matches at all.
func (r *Router) Handle(path string, method string, headers map[string]string, body []byte) (int, map[string]string, []byte, error) {
	key := method + " " + path
	handler, ok := r.handlers[key]
	if !ok {
		// check if path exists under any method → 405
		for k := range r.handlers {
			pathStart := len(k) - len(path)
			if pathStart > 0 && k[pathStart-1] == ' ' && k[len(k)-len(path):] == path {
				return 405, nil, nil, fmt.Errorf("method %q not allowed for path %q", method, path)
			}
		}
		return 404, nil, nil, fmt.Errorf("no handler registered for path %q", path)
	}
	status, respHeaders, respBody := handler(method, headers, body)
	return status, respHeaders, respBody, nil
}

// RegisterDemoHandlers adds /ping and /echo demo handlers.
func RegisterDemoHandlers(router *Router) {
	router.Register("GET", "/ping", func(method string, headers map[string]string, body []byte) (int, map[string]string, []byte) {
		return 200, map[string]string{"content-type": "text/plain"}, []byte("pong")
	})

	router.Register("POST", "/echo", func(method string, headers map[string]string, body []byte) (int, map[string]string, []byte) {
		return 200, map[string]string{"content-type": "application/octet-stream"}, body
	})
}
