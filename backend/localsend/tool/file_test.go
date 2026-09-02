package tool

import (
	"os"
	"path/filepath"
	"testing"
)

func TestValidateReceiveFolderCreatesAndCanonicalizes(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "missing", ".", "receive")
	got, err := ValidateReceiveFolder(path)
	if err != nil {
		t.Fatalf("ValidateReceiveFolder failed: %v", err)
	}
	want, err := filepath.EvalSymlinks(filepath.Join(root, "missing", "receive"))
	if err != nil {
		t.Fatalf("failed to resolve expected path: %v", err)
	}
	if got != want {
		t.Fatalf("canonical path mismatch: got %q, want %q", got, want)
	}
	if info, err := os.Stat(got); err != nil || !info.IsDir() {
		t.Fatalf("validated directory missing: %v", err)
	}
}

func TestValidateReceiveFolderRejectsRelativePath(t *testing.T) {
	if _, err := ValidateReceiveFolder("relative/receive"); err == nil {
		t.Fatal("expected relative path to be rejected")
	}
}
