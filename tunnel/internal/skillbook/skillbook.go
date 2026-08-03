// Package skillbook is the fail-closed gate between an incoming action
// request and the Zenoh bus: it is the only place that decides whether
// a skillId is real, and refuses anything it doesn't recognize before
// that request can reach the robot.
//
// Design choice: skills are loaded from one flat JSON file rather than
// the multi-file YAML profile layout suggested in the bounty wiki
// (robot.profile.yaml/skills.yaml/functions.yaml/...). For a 3-skill
// robot, one reviewable file is simpler to read end-to-end than five
// cross-referenced ones -- the wiki structure is described there as a
// recommendation for larger multi-file profiles, not a hard schema.
package skillbook

import (
	"encoding/json"
	"fmt"
	"os"
)

// Skill describes one payable (or free) action this robot exposes.
type Skill struct {
	SkillID         string                 `json:"skillId"`
	Description     string                 `json:"description"`
	PriceUSDC       string                 `json:"priceUSDC"`
	PaymentRequired bool                   `json:"paymentRequired"`
	Params          map[string]interface{} `json:"params"`
}

type fileFormat struct {
	Skills []Skill `json:"skills"`
}

// ErrUnknownSkill is returned by Resolve for any skillId not present in
// the loaded skill book. Callers must treat this as fail-closed: reject
// the request before it reaches Zenoh, never fall back to a default
// skill.
var ErrUnknownSkill = fmt.Errorf("unknown skill")

// ErrEmpty is returned by Load if the skill file parsed successfully but
// contains zero skills -- an empty allowlist must refuse everything,
// not implicitly allow everything.
var ErrEmpty = fmt.Errorf("skill book is empty")

type Book struct {
	bySkillID map[string]Skill
}

// Load reads and validates a skills.json file. An empty or missing
// skill list is an error, not a silent "allow all" -- see ErrEmpty.
func Load(path string) (*Book, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read skill book: %w", err)
	}

	var parsed fileFormat
	if err := json.Unmarshal(data, &parsed); err != nil {
		return nil, fmt.Errorf("parse skill book: %w", err)
	}

	if len(parsed.Skills) == 0 {
		return nil, ErrEmpty
	}

	book := &Book{bySkillID: make(map[string]Skill, len(parsed.Skills))}
	for _, s := range parsed.Skills {
		if s.SkillID == "" {
			return nil, fmt.Errorf("skill entry missing skillId")
		}
		book.bySkillID[s.SkillID] = s
	}
	return book, nil
}

// Resolve looks up a skillId. Returns ErrUnknownSkill for anything not
// explicitly registered -- including empty string, which callers must
// not treat as a default/no-op skill.
func (b *Book) Resolve(skillID string) (Skill, error) {
	if skillID == "" {
		return Skill{}, ErrUnknownSkill
	}
	skill, ok := b.bySkillID[skillID]
	if !ok {
		return Skill{}, ErrUnknownSkill
	}
	return skill, nil
}

// All returns every registered skill, for skill-discovery endpoints.
func (b *Book) All() []Skill {
	out := make([]Skill, 0, len(b.bySkillID))
	for _, s := range b.bySkillID {
		out = append(out, s)
	}
	return out
}
