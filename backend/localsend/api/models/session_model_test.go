package models

import (
	"path/filepath"
	"testing"
)

func TestSessionReceiveOptionsSnapshotSurvivesDefaultChange(t *testing.T) {
	firstRoot := t.TempDir()
	secondRoot := t.TempDir()
	previousRoot := GetDefaultUploadFolder()
	previousFlat := DoNotMakeSessionFolder
	defer func() {
		SetDefaultUploadFolder(previousRoot)
		DoNotMakeSessionFolder = previousFlat
	}()

	DoNotMakeSessionFolder = false
	SetDefaultUploadFolder(firstRoot)
	sessionID := "session-snapshot-test"
	SetSessionReceiveOptions(sessionID, ReceiveSessionOptions{
		DestinationRoot: firstRoot,
		ReceiveRoot:     filepath.Join(firstRoot, sessionID),
		Flat:            false,
	})
	defer RemoveUploadSession(sessionID)

	SetDefaultUploadFolder(secondRoot)
	if got, want := GetSessionReceiveRoot(sessionID), filepath.Join(firstRoot, sessionID); got != want {
		t.Fatalf("session root changed with default: got %q, want %q", got, want)
	}
	fields := GetSessionReceiveNotificationFields(sessionID)
	if got, want := fields["receiveRoot"], filepath.Join(firstRoot, sessionID); got != want {
		t.Fatalf("notification root changed with default: got %v, want %q", got, want)
	}
	if got, want := fields["destinationPath"], firstRoot; got != want {
		t.Fatalf("notification destination changed with default: got %v, want %q", got, want)
	}
}

func TestSessionReceiveRootUsesRuntimeDefaultWhenNoSnapshot(t *testing.T) {
	root := t.TempDir()
	previousRoot := GetDefaultUploadFolder()
	previousFlat := DoNotMakeSessionFolder
	defer func() {
		SetDefaultUploadFolder(previousRoot)
		DoNotMakeSessionFolder = previousFlat
	}()

	SetDefaultUploadFolder(root)
	DoNotMakeSessionFolder = true
	if got := GetSessionReceiveRoot("without-snapshot"); got != root {
		t.Fatalf("flat default root mismatch: got %q, want %q", got, root)
	}
	DoNotMakeSessionFolder = false
	if got, want := GetSessionReceiveRoot("without-snapshot"), filepath.Join(root, "without-snapshot"); got != want {
		t.Fatalf("session default root mismatch: got %q, want %q", got, want)
	}
}
