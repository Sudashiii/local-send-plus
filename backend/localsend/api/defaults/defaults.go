package defaults

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/moyoez/localsend-go/api/models"
	"github.com/moyoez/localsend-go/notify"
	"github.com/moyoez/localsend-go/tool"
	"github.com/moyoez/localsend-go/types"
)

// DefaultOnRegister is the default callback for device register.
func DefaultOnRegister(remote *types.VersionMessage) error {
	tool.DefaultLogger.Infof("Received device register request: %s (fingerprint: %s, port: %d)",
		remote.Alias, remote.Fingerprint, remote.Port)
	return nil
}

// DefaultOnPrepareUpload is the default callback for prepare-upload.
func DefaultOnPrepareUpload(request *types.PrepareUploadRequest, pin string) (*types.PrepareUploadResponse, error) {
	tool.DefaultLogger.Infof("Received file transfer prepare request: from %s, file count: %d, PIN: %s",
		request.Info.Alias, len(request.Files), pin)

	askSession := tool.GenerateRandomUUID()
	response := &types.PrepareUploadResponse{
		SessionId: askSession,
		Files:     make(map[string]string),
	}

	pinSetted := tool.GetProgramConfigStatus().Pin
	switch {
	case pinSetted != "" && pin == "":
		notification := &types.Notification{
			Type:    types.NotifyTypePinRequired,
			Title:   "PIN Required",
			Message: fmt.Sprintf("PIN required for incoming files from %s", request.Info.Alias),
			Data: map[string]any{
				"from":      request.Info.Alias,
				"fileCount": len(request.Files),
			},
		}
		tool.DefaultLogger.Infof("[Notify] Sending pin_required notification: %v", notification)
		if err := notify.SendNotification(notification, ""); err != nil {
			tool.DefaultLogger.Errorf("[Notify] Failed to send pin_required notification: %v", err)
		}
		return nil, fmt.Errorf("pin required")
	case pinSetted != "" && pin != pinSetted:
		return nil, fmt.Errorf("invalid PIN")
	}

	// Text-only message: single file, text/plain, with preview — show dialog, wait for user dismiss, then return 204 (no upload)
	if len(request.Files) == 1 {
		for _, info := range request.Files {
			if strings.TrimSpace(strings.ToLower(info.FileType)) == "text/plain" && info.Preview != "" {
				title := "Text Received"
				if request.Info.Alias != "" {
					title = fmt.Sprintf("From %s", request.Info.Alias)
				}
				textDismissSessionId := tool.GenerateRandomUUID()
				dismissCh := make(chan struct{}, 1)
				models.SetTextReceivedDismissChannel(textDismissSessionId, dismissCh)
				defer models.DeleteTextReceivedDismissChannel(textDismissSessionId)
				if err := notify.SendTextReceivedNotification(request.Info.Alias, title, info.Preview, info.FileName, textDismissSessionId); err != nil {
					tool.DefaultLogger.Errorf("[Notify] Failed to send text_received notification: %v", err)
					return nil, nil
				}
				dismissTimeout := 2 * time.Minute
				select {
				case <-dismissCh:
					tool.DefaultLogger.Infof("[PrepareUpload] Text-only message from %s dismissed by user, returning 204 (no upload)", request.Info.Alias)
				case <-time.After(dismissTimeout):
					tool.DefaultLogger.Infof("[PrepareUpload] Text-only message from %s dismiss timeout, returning 204 (no upload)", request.Info.Alias)
				}
				return nil, nil
			}
			break
		}
	}

	programConfig := tool.GetProgramConfigStatus()
	needConfirmation := !programConfig.AutoSave
	if needConfirmation && programConfig.AutoSaveFromFavorites {
		if tool.IsFavorite(request.Info.Fingerprint) {
			tool.DefaultLogger.Infof("Auto-accepting from favorite device: %s (fingerprint: %s)", request.Info.Alias, request.Info.Fingerprint)
			needConfirmation = false
		}
	}

	destinationRoot := models.GetDefaultUploadFolder()
	if needConfirmation {
		confirmCh := make(chan types.ConfirmResult, 1)
		models.SetConfirmRecvChannel(askSession, confirmCh)
		defer models.DeleteConfirmRecvChannel(askSession)

		// Only collect first MaxNotifyFiles for notify payload, keep full FileInfo
		maxFiles := min(len(request.Files), notify.MaxNotifyFiles)
		files := make([]types.FileInfo, 0, maxFiles)
		for _, info := range request.Files {
			if len(files) >= notify.MaxNotifyFiles {
				break
			}
			files = append(files, info)
		}

		notification := &types.Notification{
			Type:    types.NotifyTypeConfirmRecv,
			Title:   "Confirm Receive",
			Message: fmt.Sprintf("Incoming files from %s", request.Info.Alias),
			Data: map[string]any{
				"sessionId":  askSession,
				"from":       request.Info.Alias,
				"fileCount":  len(request.Files),
				"totalFiles": len(request.Files),
				"files":      files,
				"expiresAt":  time.Now().Add(2 * time.Minute).Unix(),
			},
		}
		tool.DefaultLogger.Infof("[Notify] Sending confirm_recv notification: %v", notification)
		tool.DefaultLogger.Debugf("Accpet by using this link: https://localhost:53317/api/self/v1/confirm-recv?sessionId=%s&confirmed=true", askSession)
		tool.DefaultLogger.Debugf("Reject by using this link: https://localhost:53317/api/self/v1/confirm-recv?sessionId=%s&confirmed=false", askSession)
		if err := notify.SendNotification(notification, ""); err != nil {
			tool.DefaultLogger.Errorf("[Notify] Failed to send confirm_recv notification: %v", err)
		}
		confirmTimeout := 2 * time.Minute
		confirmTimeOuttimer := time.NewTimer(confirmTimeout)
		defer confirmTimeOuttimer.Stop()
		select {
		case result := <-confirmCh:
			if !result.Confirmed {
				return nil, fmt.Errorf("rejected")
			}
			if strings.TrimSpace(result.DestinationPath) != "" {
				destinationRoot = result.DestinationPath
			}
		case <-confirmTimeOuttimer.C:
			return nil, fmt.Errorf("rejected")
		}
	}

	validatedRoot, err := tool.ValidateReceiveFolder(destinationRoot)
	if err != nil {
		tool.DefaultLogger.Errorf("[PrepareUpload] Invalid receive folder %q: %v", destinationRoot, err)
		return nil, fmt.Errorf("invalid receive folder: %w", err)
	}
	flat := models.DoNotMakeSessionFolder
	receiveRoot := validatedRoot
	if !flat {
		receiveRoot = filepath.Join(validatedRoot, askSession)
	}

	if err := tool.JoinSession(askSession); err != nil {
		return nil, err
	}
	models.SetSessionReceiveOptions(askSession, models.ReceiveSessionOptions{
		DestinationRoot: validatedRoot,
		ReceiveRoot:     receiveRoot,
		Flat:            flat,
	})

	models.CreateSessionContext(askSession)

	for fileID := range request.Files {
		response.Files[fileID] = "accepted"
	}

	models.CacheUploadSession(askSession, request.Files)

	return response, nil
}

// DefaultOnUpload is the default callback for file upload.
func DefaultOnUpload(sessionId, fileId, token string, data io.Reader, remoteAddr string) error {
	if models.IsSessionCancelled(sessionId) {
		return fmt.Errorf("session cancelled")
	}

	ctx := models.GetSessionContext(sessionId)
	if ctx == nil {
		ctx = context.Background()
	}

	info, ok := models.LookupFileInfo(sessionId, fileId)
	if !ok {
		return fmt.Errorf("file metadata not found")
	}
	// Cancellation can race the first check above.  Re-check after loading
	// metadata and snapshot the session options locally so cleanup cannot make
	// this upload fall back to a newly-changed global default.
	if models.IsSessionCancelled(sessionId) {
		return fmt.Errorf("session cancelled")
	}

	options, hasSessionOptions := models.GetSessionReceiveOptions(sessionId)
	uploadDir := ""
	if hasSessionOptions {
		uploadDir = options.ReceiveRoot
	} else {
		uploadDir = models.GetSessionReceiveRoot(sessionId)
	}
	if uploadDir == "" {
		return fmt.Errorf("receive folder not configured")
	}
	if err := os.MkdirAll(uploadDir, 0o755); err != nil {
		return fmt.Errorf("create upload dir failed: %w", err)
	}

	fileName := strings.TrimSpace(info.FileName)
	if fileName == "" {
		fileName = fileId
	}
	// LocalSend folder uploads encode a relative path using forward slashes.
	// Reject absolute names and parent components before filepath.Clean can
	// normalize them into a path that appears to be inside the receive root.
	// This keeps traversal attempts observable and prevents symlink/parent
	// ambiguity from being turned into a different filename.
	protocolName := strings.ReplaceAll(fileName, "\\", "/")
	if strings.HasPrefix(protocolName, "/") || strings.ContainsRune(protocolName, 0) {
		return fmt.Errorf("invalid file path: absolute or malformed filename")
	}
	for _, segment := range strings.Split(protocolName, "/") {
		if segment == ".." {
			return fmt.Errorf("invalid file path: path traversal not allowed")
		}
	}
	// Preserve relative path (e.g. "foldername/subdir/file.txt") for folder uploads
	relativePath := filepath.Clean(filepath.FromSlash(protocolName))
	if relativePath == "." || relativePath == string(filepath.Separator) {
		return fmt.Errorf("invalid file path: empty filename")
	}
	sep := string(filepath.Separator)
	firstIdx := strings.Index(relativePath, sep)
	isFolderUpload := firstIdx >= 0
	var targetPath string
	if isFolderUpload {
		firstSegment := relativePath[:firstIdx]
		rest := relativePath[firstIdx+len(sep):]
		resolved := models.GetResolvedReceiveFolder(sessionId, firstSegment)
		if resolved == "" {
			candidateDir := filepath.Join(uploadDir, firstSegment)
			if _, err := os.Stat(candidateDir); err == nil {
				resolved = tool.NextAvailableDir(uploadDir, firstSegment)
			} else {
				resolved = firstSegment
			}
			models.SetResolvedReceiveFolder(sessionId, firstSegment, resolved)
		}
		targetPath = filepath.Join(uploadDir, resolved, rest)
	} else {
		targetPath = filepath.Join(uploadDir, relativePath)
	}
	// Prevent path traversal: ensure result stays under uploadDir
	uploadDirAbs, err := filepath.Abs(uploadDir)
	if err != nil {
		return fmt.Errorf("upload dir abs: %w", err)
	}
	targetPathAbs, err := filepath.Abs(targetPath)
	if err != nil {
		return fmt.Errorf("target path abs: %w", err)
	}
	rel, err := filepath.Rel(uploadDirAbs, targetPathAbs)
	if err != nil || strings.HasPrefix(rel, "..") || rel == ".." {
		return fmt.Errorf("invalid file path: path traversal not allowed")
	}
	// Create parent directories for folder structure
	if err := os.MkdirAll(filepath.Dir(targetPath), 0o755); err != nil {
		return fmt.Errorf("create parent dir failed: %w", err)
	}
	// A symlink created below the receive root must not redirect an upload to
	// another filesystem location.  Resolve the parent after creating it and
	// compare it with the canonical session root.
	canonicalUploadDir, err := filepath.EvalSymlinks(uploadDirAbs)
	if err != nil {
		return fmt.Errorf("resolve upload dir: %w", err)
	}
	canonicalParent, err := filepath.EvalSymlinks(filepath.Dir(targetPath))
	if err != nil {
		return fmt.Errorf("resolve target parent: %w", err)
	}
	canonicalRel, err := filepath.Rel(canonicalUploadDir, canonicalParent)
	if err != nil || canonicalRel == ".." || strings.HasPrefix(canonicalRel, ".."+string(filepath.Separator)) || filepath.IsAbs(canonicalRel) {
		return fmt.Errorf("invalid file path: target parent escapes receive root")
	}
	if targetInfo, err := os.Lstat(targetPath); err == nil && targetInfo.Mode()&os.ModeSymlink != 0 {
		return fmt.Errorf("invalid file path: target is a symbolic link")
	}
	// For single-file (non-folder) with DoNotMakeSessionFolder, use NextAvailablePath for file name collision.
	// For folder uploads we already resolved the folder name; do not rename files inside.
	flat := models.DoNotMakeSessionFolder
	if hasSessionOptions {
		flat = options.Flat
	}
	if flat && !isFolderUpload {
		targetPath = tool.NextAvailablePath(filepath.Dir(targetPath), filepath.Base(targetPath))
	}

	// O_EXCL makes the final create operation no-overwrite even if another
	// upload (or process) races the collision check above.  A retry can choose
	// another name rather than replacing an existing file.
	file, err := os.OpenFile(targetPath, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o644)
	if err != nil {
		return fmt.Errorf("create file failed: %w", err)
	}
	removePartial := func() {
		_ = file.Close()
		_ = os.Remove(targetPath)
	}
	defer func() {
		if err := file.Close(); err != nil {
			tool.DefaultLogger.Warnf("Failed to close file: %v", err)
		}
	}()

	hasher := sha256.New()
	writer := io.MultiWriter(file, hasher)

	var written int64
	// When data is io.Closer (e.g. http.Request.Body), close it on context cancel so that
	// blocking Read() unblocks and in-flight upload can be interrupted immediately.
	if closer, ok := data.(io.Closer); ok {
		type copyResult struct {
			n   int64
			err error
		}
		ch := make(chan copyResult, 1)
		go func() {
			n, e := tool.CopyWithContext(ctx, writer, data)
			ch <- copyResult{n, e}
		}()
		select {
		case res := <-ch:
			written, err = res.n, res.err
		case <-ctx.Done():
			_ = closer.Close()
			res := <-ch
			written, err = res.n, ctx.Err()
		}
	} else {
		written, err = tool.CopyWithContext(ctx, writer, data)
	}
	if err != nil {
		if ctx.Err() != nil {
			removePartial()
			return fmt.Errorf("upload cancelled")
		}
		removePartial()
		return fmt.Errorf("write file failed: %w", err)
	}

	if ctx.Err() != nil {
		removePartial()
		return fmt.Errorf("upload cancelled")
	}

	if info.Size > 0 && written != info.Size {
		removePartial()
		return fmt.Errorf("size mismatch")
	}

	if info.SHA256 != "" {
		actual := hex.EncodeToString(hasher.Sum(nil))
		if !strings.EqualFold(actual, info.SHA256) {
			removePartial()
			return fmt.Errorf("hash mismatch")
		}
	}

	models.SetFileSavePath(sessionId, fileId, targetPath)
	tool.DefaultLogger.Infof("Upload saved: sessionId=%s, fileId=%s, path=%s", sessionId, fileId, targetPath)
	return nil
}

// DefaultOnCancel is the default callback for session cancel.
func DefaultOnCancel(sessionId string) error {
	tool.DefaultLogger.Infof("Received file transfer cancel request: sessionId=%s", sessionId)
	if !tool.QuerySessionIsValid(sessionId) {
		return fmt.Errorf("session %s not found", sessionId)
	}
	models.RemoveUploadSession(sessionId)
	tool.DestorySession(sessionId)
	tool.DefaultLogger.Infof("Session %s canceled and all ongoing uploads interrupted", sessionId)
	return nil
}
