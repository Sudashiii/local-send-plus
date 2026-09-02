package defaults

import (
	"bytes"
	"os"
	"path/filepath"
	"testing"

	"github.com/moyoez/localsend-go/api/models"
	"github.com/moyoez/localsend-go/tool"
	"github.com/moyoez/localsend-go/types"
)

func TestDefaultOnUploadUsesCapturedSessionRoot(t *testing.T) {
	firstRoot := t.TempDir()
	secondRoot := t.TempDir()
	previousRoot := models.GetDefaultUploadFolder()
	previousFlat := models.DoNotMakeSessionFolder
	defer func() {
		models.SetDefaultUploadFolder(previousRoot)
		models.DoNotMakeSessionFolder = previousFlat
	}()

	models.SetDefaultUploadFolder(secondRoot)
	models.DoNotMakeSessionFolder = false
	sessionID := "upload-routing-test"
	fileID := "file-routing-test"
	models.SetSessionReceiveOptions(sessionID, models.ReceiveSessionOptions{
		DestinationRoot: firstRoot,
		ReceiveRoot:     filepath.Join(firstRoot, sessionID),
		Flat:            false,
	})
	models.CacheUploadSession(sessionID, map[string]types.FileInfo{
		fileID: {ID: fileID, FileName: "folder/payload.bin", Size: 7, FileType: "application/octet-stream"},
	})
	models.CreateSessionContext(sessionID)
	if err := tool.JoinSession(sessionID); err != nil {
		t.Fatal(err)
	}
	defer func() {
		models.RemoveUploadSession(sessionID)
		tool.DestorySession(sessionID)
	}()

	if err := DefaultOnUpload(sessionID, fileID, "", bytes.NewReader([]byte("payload")), "127.0.0.1"); err != nil {
		t.Fatalf("upload failed: %v", err)
	}
	want := filepath.Join(firstRoot, sessionID, "folder", "payload.bin")
	if _, err := os.Stat(want); err != nil {
		t.Fatalf("captured destination was not used (%s): %v", want, err)
	}
	if _, err := os.Stat(filepath.Join(secondRoot, sessionID, "folder", "payload.bin")); !os.IsNotExist(err) {
		t.Fatalf("runtime default unexpectedly received the file")
	}
}

func TestDefaultOnUploadRejectsTraversalAndCleansPartialFiles(t *testing.T) {
	root := t.TempDir()
	previousRoot := models.GetDefaultUploadFolder()
	previousFlat := models.DoNotMakeSessionFolder
	defer func() {
		models.SetDefaultUploadFolder(previousRoot)
		models.DoNotMakeSessionFolder = previousFlat
	}()

	models.SetDefaultUploadFolder(root)
	models.DoNotMakeSessionFolder = true
	traversalSession := "upload-traversal-test"
	traversalFile := "traversal-file"
	models.SetSessionReceiveOptions(traversalSession, models.ReceiveSessionOptions{
		DestinationRoot: root,
		ReceiveRoot:     root,
		Flat:            true,
	})
	models.CacheUploadSession(traversalSession, map[string]types.FileInfo{
		traversalFile: {ID: traversalFile, FileName: "../escape.txt", Size: 6, FileType: "text/plain"},
	})
	models.CreateSessionContext(traversalSession)
	if err := tool.JoinSession(traversalSession); err != nil {
		t.Fatal(err)
	}
	defer func() {
		models.RemoveUploadSession(traversalSession)
		tool.DestorySession(traversalSession)
	}()
	if err := DefaultOnUpload(traversalSession, traversalFile, "", bytes.NewReader([]byte("escape")), "127.0.0.1"); err == nil {
		t.Fatal("expected traversal filename to be rejected")
	}
	if _, err := os.Stat(filepath.Join(root, "escape.txt")); !os.IsNotExist(err) {
		t.Fatalf("traversal filename created an unexpected file: %v", err)
	}

	partialSession := "upload-partial-cleanup-test"
	partialFile := "partial-file"
	partialPath := filepath.Join(root, "partial.bin")
	models.SetSessionReceiveOptions(partialSession, models.ReceiveSessionOptions{
		DestinationRoot: root,
		ReceiveRoot:     root,
		Flat:            true,
	})
	models.CacheUploadSession(partialSession, map[string]types.FileInfo{
		partialFile: {ID: partialFile, FileName: "partial.bin", Size: 99, FileType: "application/octet-stream"},
	})
	models.CreateSessionContext(partialSession)
	if err := tool.JoinSession(partialSession); err != nil {
		t.Fatal(err)
	}
	defer func() {
		models.RemoveUploadSession(partialSession)
		tool.DestorySession(partialSession)
	}()
	if err := DefaultOnUpload(partialSession, partialFile, "", bytes.NewReader([]byte("short")), "127.0.0.1"); err == nil {
		t.Fatal("expected size mismatch")
	}
	if _, err := os.Stat(partialPath); !os.IsNotExist(err) {
		t.Fatalf("partial upload was not removed: %v", err)
	}
}
