package zenohconfig

import (
	"encoding/json"
	"os"

	"github.com/eclipse-zenoh/zenoh-go/zenoh"
)

// FromEnvironment uses Zenoh discovery by default. ZENOH_CONNECT_ENDPOINT
// provides a deterministic TCP peer for containers and reviewer evidence.
func FromEnvironment() (zenoh.Config, error) {
	endpoint := os.Getenv("ZENOH_CONNECT_ENDPOINT")
	if endpoint == "" {
		return zenoh.NewConfigDefault(), nil
	}
	encoded, err := json.Marshal(map[string]any{
		"connect": map[string]any{"endpoints": []string{endpoint}},
	})
	if err != nil {
		var empty zenoh.Config
		return empty, err
	}
	return zenoh.NewConfigFromStr(string(encoded))
}
