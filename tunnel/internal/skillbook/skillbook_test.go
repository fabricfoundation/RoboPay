package skillbook

import (
	"os"
	"path/filepath"
	"testing"
)

func writeFixture(t *testing.T, content string) string {
	t.Helper()
	dir := t.TempDir()
	path := filepath.Join(dir, "skills.json")
	if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
		t.Fatalf("write fixture: %v", err)
	}
	return path
}

func TestLoad_ValidFile(t *testing.T) {
	path := writeFixture(t, `{
		"skills": [
			{"skillId": "look_at_apple", "description": "x", "priceUSDC": "0.001", "paymentRequired": true, "params": {}},
			{"skillId": "stop", "description": "x", "priceUSDC": "0.000", "paymentRequired": false, "params": {}}
		]
	}`)

	book, err := Load(path)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(book.All()) != 2 {
		t.Fatalf("expected 2 skills, got %d", len(book.All()))
	}
}

func TestLoad_EmptySkillsIsError(t *testing.T) {
	path := writeFixture(t, `{"skills": []}`)
	_, err := Load(path)
	if err != ErrEmpty {
		t.Fatalf("expected ErrEmpty for empty skill list, got %v", err)
	}
}

func TestLoad_MissingFile(t *testing.T) {
	_, err := Load("/nonexistent/skills.json")
	if err == nil {
		t.Fatalf("expected error for missing file")
	}
}

func TestResolve_KnownSkill(t *testing.T) {
	path := writeFixture(t, `{"skills": [{"skillId": "look_at_apple", "description": "x", "priceUSDC": "0.001", "paymentRequired": true, "params": {}}]}`)
	book, _ := Load(path)

	skill, err := book.Resolve("look_at_apple")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if skill.PriceUSDC != "0.001" {
		t.Fatalf("expected price 0.001, got %s", skill.PriceUSDC)
	}
}

func TestResolve_UnknownSkillRejected(t *testing.T) {
	path := writeFixture(t, `{"skills": [{"skillId": "look_at_apple", "description": "x", "priceUSDC": "0.001", "paymentRequired": true, "params": {}}]}`)
	book, _ := Load(path)

	_, err := book.Resolve("do_a_backflip")
	if err != ErrUnknownSkill {
		t.Fatalf("expected ErrUnknownSkill, got %v", err)
	}
}

func TestResolve_EmptySkillIDRejected(t *testing.T) {
	// An empty/missing action field must fail closed, not silently map
	// to some default behavior.
	path := writeFixture(t, `{"skills": [{"skillId": "stop", "description": "x", "priceUSDC": "0.000", "paymentRequired": false, "params": {}}]}`)
	book, _ := Load(path)

	_, err := book.Resolve("")
	if err != ErrUnknownSkill {
		t.Fatalf("expected ErrUnknownSkill for empty skillId, got %v", err)
	}
}
