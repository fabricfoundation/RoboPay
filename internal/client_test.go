package internal

import (
	"context"
	"testing"
	"time"

	"go.uber.org/zap"
)

func TestDialInvalidBaseURL(t *testing.T) {
	client := NewClient("://bad-url", "robot-1", nil, zap.NewNop())

	_, _, err := client.dial(context.Background())
	if err == nil {
		t.Fatal("expected error for invalid ws base url")
	}
}

func TestSleepWithContextCancelled(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()

	if ok := sleepWithContext(ctx, 500*time.Millisecond); ok {
		t.Fatal("expected sleepWithContext to return false when context is cancelled")
	}
}

func TestSleepWithContextCompletes(t *testing.T) {
	ctx := context.Background()

	if ok := sleepWithContext(ctx, 5*time.Millisecond); !ok {
		t.Fatal("expected sleepWithContext to return true when timer completes")
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
