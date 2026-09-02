package models

import (
	"context"
	"maps"
	"path/filepath"
	"sync"

	ttlworker "github.com/FloatTech/ttl"
	"github.com/moyoez/localsend-go/tool"
	"github.com/moyoez/localsend-go/types"
)

var (
	uploadSessionMu          sync.RWMutex
	DefaultUploadFolder      = "uploads"
	defaultUploadFolderMu    sync.RWMutex
	DoNotMakeSessionFolder   bool // if true, save under upload folder only; same filename -> name-2.ext, name-3.ext, ...
	uploadSessions           = ttlworker.NewCache[string, map[string]types.FileInfo](tool.DefaultTTL)
	uploadValidated          = ttlworker.NewCache[string, bool](tool.DefaultTTL)
	confirmRecvChans         = ttlworker.NewCache[string, chan types.ConfirmResult](tool.DefaultTTL)
	textReceivedDismissChans = ttlworker.NewCache[string, chan struct{}](tool.DefaultTTL)
	v1Sessions               = ttlworker.NewCache[string, string](tool.DefaultTTL)
	// sessionContexts stores the context for each session to support cancellation
	sessionContexts = ttlworker.NewCache[string, *types.SessionContext](tool.DefaultTTL)
	// uploadStats tracks success/failure counts per session
	uploadStats = ttlworker.NewCache[string, *types.SessionUploadStats](tool.DefaultTTL)
	// fileSavePaths stores actual save path per (sessionId, fileId) for notifications
	fileSavePaths = ttlworker.NewCache[string, map[string]string](tool.DefaultTTL)
	// resolvedReceiveFolders stores resolved top-level folder name per (sessionId, firstSegment) when folder name collides
	resolvedReceiveFolders = ttlworker.NewCache[string, map[string]string](tool.DefaultTTL)
	// receiveSessionOptions captures the receive root at prepare time.
	receiveSessionOptions = ttlworker.NewCache[string, ReceiveSessionOptions](tool.DefaultTTL)
)

// ReceiveSessionOptions is immutable routing metadata captured for one
// incoming transfer.
type ReceiveSessionOptions struct {
	DestinationRoot string `json:"destinationPath"`
	ReceiveRoot     string `json:"root"`
	DestinationID   string `json:"destinationId,omitempty"`
	DestinationName string `json:"destinationName,omitempty"`
	Flat            bool   `json:"flat"`
}

// SetDefaultUploadFolder updates the default for future receive sessions.
func SetDefaultUploadFolder(folder string) {
	defaultUploadFolderMu.Lock()
	DefaultUploadFolder = folder
	defaultUploadFolderMu.Unlock()
}

// GetDefaultUploadFolder returns the current default receive root.
func GetDefaultUploadFolder() string {
	defaultUploadFolderMu.RLock()
	defer defaultUploadFolderMu.RUnlock()
	return DefaultUploadFolder
}

// SetSessionReceiveOptions records the destination selected for a session.
func SetSessionReceiveOptions(sessionId string, options ReceiveSessionOptions) {
	uploadSessionMu.Lock()
	defer uploadSessionMu.Unlock()
	receiveSessionOptions.Set(sessionId, options)
}

// GetSessionReceiveOptions returns session routing metadata, if present.
func GetSessionReceiveOptions(sessionId string) (ReceiveSessionOptions, bool) {
	uploadSessionMu.RLock()
	defer uploadSessionMu.RUnlock()
	options := receiveSessionOptions.Get(sessionId)
	if options.ReceiveRoot == "" {
		return ReceiveSessionOptions{}, false
	}
	return options, true
}

// GetSessionReceiveRoot returns the actual upload directory for a session.
func GetSessionReceiveRoot(sessionId string) string {
	if options, ok := GetSessionReceiveOptions(sessionId); ok {
		return options.ReceiveRoot
	}
	root := GetDefaultUploadFolder()
	if DoNotMakeSessionFolder {
		return root
	}
	return filepath.Join(root, sessionId)
}

// GetSessionReceiveFlat returns the layout captured for a session.
func GetSessionReceiveFlat(sessionId string) bool {
	if options, ok := GetSessionReceiveOptions(sessionId); ok {
		return options.Flat
	}
	return DoNotMakeSessionFolder
}

// GetSessionReceiveNotificationFields returns routing fields shared by
// upload_start and upload_end notifications.
func GetSessionReceiveNotificationFields(sessionId string) map[string]any {
	options, ok := GetSessionReceiveOptions(sessionId)
	if !ok {
		root := GetDefaultUploadFolder()
		return map[string]any{
			"doNotMakeSessionFolder": DoNotMakeSessionFolder,
			"uploadFolder":           root,
			"receiveRoot":            GetSessionReceiveRoot(sessionId),
			"destinationPath":        root,
		}
	}
	return map[string]any{
		"doNotMakeSessionFolder": options.Flat,
		"uploadFolder":           options.DestinationRoot,
		"receiveRoot":            options.ReceiveRoot,
		"destinationPath":        options.DestinationRoot,
		"destinationId":          options.DestinationID,
		"destinationName":        options.DestinationName,
		"layout":                 map[bool]string{true: "flat", false: "session"}[options.Flat],
	}
}

func CacheUploadSession(sessionId string, files map[string]types.FileInfo) {
	uploadSessionMu.Lock()
	defer uploadSessionMu.Unlock()
	copied := make(map[string]types.FileInfo, len(files))
	maps.Copy(copied, files)
	uploadSessions.Set(sessionId, copied)
}

func LookupFileInfo(sessionId, fileId string) (types.FileInfo, bool) {
	uploadSessionMu.RLock()
	defer uploadSessionMu.RUnlock()
	files := uploadSessions.Get(sessionId)
	if files == nil {
		return types.FileInfo{}, false
	}
	info, exists := files[fileId]
	return info, exists
}

func RemoveUploadedFile(sessionId, fileId string) {
	uploadSessionMu.Lock()
	defer uploadSessionMu.Unlock()
	files := uploadSessions.Get(sessionId)
	if files == nil {
		return
	}
	delete(files, fileId)
	if len(files) == 0 {
		uploadSessions.Delete(sessionId)
		return
	}
	uploadSessions.Set(sessionId, files)
}

// InitSessionStats initializes upload statistics for a session
func InitSessionStats(sessionId string, totalFiles int) {
	uploadSessionMu.Lock()
	defer uploadSessionMu.Unlock()
	uploadStats.Set(sessionId, &types.SessionUploadStats{
		TotalFiles:    totalFiles,
		SuccessFiles:  0,
		FailedFiles:   0,
		FailedFileIds: nil,
	})
}

// MarkFileUploadedAndCheckComplete marks a file as uploaded (success or failure) and returns
// (remaining, isLast, stats) to help determine if all files are done
func MarkFileUploadedAndCheckComplete(sessionId, fileId string, success bool) (remaining int, isLast bool, stats *types.SessionUploadStats) {
	uploadSessionMu.Lock()
	defer uploadSessionMu.Unlock()

	files := uploadSessions.Get(sessionId)
	if files == nil {
		return 0, true, nil
	}

	// Update stats
	sessionStats := uploadStats.Get(sessionId)
	if sessionStats == nil {
		sessionStats = &types.SessionUploadStats{
			TotalFiles:    len(files),
			FailedFileIds: nil,
		}
	}

	if success {
		sessionStats.SuccessFiles++
	} else {
		sessionStats.FailedFiles++
		sessionStats.FailedFileIds = append(sessionStats.FailedFileIds, fileId)
	}
	uploadStats.Set(sessionId, sessionStats)

	// Remove from pending files
	delete(files, fileId)
	remaining = len(files)
	isLast = remaining == 0

	if isLast {
		uploadSessions.Delete(sessionId)
		// Keep stats for the notification, will be cleaned up later
	} else {
		uploadSessions.Set(sessionId, files)
	}

	return remaining, isLast, sessionStats
}

// GetSessionStats returns the upload statistics for a session
func GetSessionStats(sessionId string) *types.SessionUploadStats {
	uploadSessionMu.RLock()
	defer uploadSessionMu.RUnlock()
	return uploadStats.Get(sessionId)
}

// SetFileSavePath stores the actual save path for a file (used by notifications when DoNotMakeSessionFolder or name collision).
func SetFileSavePath(sessionId, fileId, savePath string) {
	uploadSessionMu.Lock()
	defer uploadSessionMu.Unlock()
	m := fileSavePaths.Get(sessionId)
	if m == nil {
		m = make(map[string]string)
		fileSavePaths.Set(sessionId, m)
	}
	m[fileId] = savePath
}

// GetFileSavePath returns the stored save path for a file, if any.
func GetFileSavePath(sessionId, fileId string) (string, bool) {
	uploadSessionMu.RLock()
	defer uploadSessionMu.RUnlock()
	m := fileSavePaths.Get(sessionId)
	if m == nil {
		return "", false
	}
	path, ok := m[fileId]
	return path, ok
}

// GetSessionSavePaths returns a copy of all fileId -> savePath for the session (for upload_end notification).
func GetSessionSavePaths(sessionId string) map[string]string {
	uploadSessionMu.RLock()
	defer uploadSessionMu.RUnlock()
	m := fileSavePaths.Get(sessionId)
	if len(m) == 0 {
		return nil
	}
	out := make(map[string]string, len(m))
	maps.Copy(out, m)
	return out
}

// GetSessionReceiveManifest returns the session routing metadata and a copy
// of every successful file path.  It is intentionally available until the
// upload-end notification has been acknowledged by the local plugin.
func GetSessionReceiveManifest(sessionId string) (ReceiveSessionOptions, map[string]string, bool) {
	options, ok := GetSessionReceiveOptions(sessionId)
	if !ok {
		return ReceiveSessionOptions{}, nil, false
	}
	return options, GetSessionSavePaths(sessionId), true
}

// GetResolvedReceiveFolder returns the cached resolved folder name for (sessionId, firstSegment), or empty string if not cached.
func GetResolvedReceiveFolder(sessionId, firstSegment string) string {
	uploadSessionMu.RLock()
	defer uploadSessionMu.RUnlock()
	m := resolvedReceiveFolders.Get(sessionId)
	if m == nil {
		return ""
	}
	return m[firstSegment]
}

// SetResolvedReceiveFolder caches the resolved folder name for (sessionId, firstSegment).
func SetResolvedReceiveFolder(sessionId, firstSegment, resolved string) {
	uploadSessionMu.Lock()
	defer uploadSessionMu.Unlock()
	m := resolvedReceiveFolders.Get(sessionId)
	if m == nil {
		m = make(map[string]string)
		resolvedReceiveFolders.Set(sessionId, m)
	}
	m[firstSegment] = resolved
}

// CleanupSessionStats removes the upload statistics for a session
func CleanupSessionStats(sessionId string) {
	uploadSessionMu.Lock()
	defer uploadSessionMu.Unlock()
	uploadStats.Delete(sessionId)
}

func RemoveUploadSession(sessionId string) {
	uploadSessionMu.Lock()
	defer uploadSessionMu.Unlock()
	uploadSessions.Delete(sessionId)
	uploadValidated.Delete(sessionId)
	confirmRecvChans.Delete(sessionId)
	fileSavePaths.Delete(sessionId)
	resolvedReceiveFolders.Delete(sessionId)
	receiveSessionOptions.Delete(sessionId)
	// Cancel the session context to interrupt ongoing uploads
	if sessCtx := sessionContexts.Get(sessionId); sessCtx != nil {
		sessCtx.Cancel()
		sessionContexts.Delete(sessionId)
	}
}

func IsSessionValidated(sessionId string) bool {
	uploadSessionMu.RLock()
	defer uploadSessionMu.RUnlock()
	return uploadValidated.Get(sessionId)
}

func MarkSessionValidated(sessionId string) {
	uploadSessionMu.Lock()
	defer uploadSessionMu.Unlock()
	uploadValidated.Set(sessionId, true)
}

func SetConfirmRecvChannel(sessionId string, ch chan types.ConfirmResult) {
	uploadSessionMu.Lock()
	defer uploadSessionMu.Unlock()
	confirmRecvChans.Set(sessionId, ch)
}

func GetConfirmRecvChannel(sessionId string) (chan types.ConfirmResult, bool) {
	uploadSessionMu.RLock()
	defer uploadSessionMu.RUnlock()
	ch := confirmRecvChans.Get(sessionId)
	if ch == nil {
		return nil, false
	}
	return ch, true
}

func DeleteConfirmRecvChannel(sessionId string) {
	uploadSessionMu.Lock()
	defer uploadSessionMu.Unlock()
	confirmRecvChans.Delete(sessionId)
}

func SetTextReceivedDismissChannel(sessionId string, ch chan struct{}) {
	uploadSessionMu.Lock()
	defer uploadSessionMu.Unlock()
	textReceivedDismissChans.Set(sessionId, ch)
}

func GetTextReceivedDismissChannel(sessionId string) (chan struct{}, bool) {
	uploadSessionMu.RLock()
	defer uploadSessionMu.RUnlock()
	ch := textReceivedDismissChans.Get(sessionId)
	if ch == nil {
		return nil, false
	}
	return ch, true
}

func DeleteTextReceivedDismissChannel(sessionId string) {
	uploadSessionMu.Lock()
	defer uploadSessionMu.Unlock()
	textReceivedDismissChans.Delete(sessionId)
}

func GetUploadSessionFiles(sessionId string) (map[string]types.FileInfo, bool) {
	uploadSessionMu.RLock()
	defer uploadSessionMu.RUnlock()
	files := uploadSessions.Get(sessionId)
	if files == nil {
		return nil, false
	}
	copied := make(map[string]types.FileInfo, len(files))
	maps.Copy(copied, files)
	return copied, true
}

// StoreV1Session stores the IP -> sessionId mapping for V1 protocol
func StoreV1Session(ip, sessionId string) {
	uploadSessionMu.Lock()
	defer uploadSessionMu.Unlock()
	v1Sessions.Set(ip, sessionId)
}

// GetV1Session retrieves the sessionId for the given IP address (V1 protocol)
func GetV1Session(ip string) string {
	uploadSessionMu.RLock()
	defer uploadSessionMu.RUnlock()
	return v1Sessions.Get(ip)
}

// RemoveV1Session removes the IP -> sessionId mapping for V1 protocol
func RemoveV1Session(ip string) {
	uploadSessionMu.Lock()
	defer uploadSessionMu.Unlock()
	v1Sessions.Delete(ip)
}

// CreateSessionContext creates a new context for the session and returns it
func CreateSessionContext(sessionId string) context.Context {
	uploadSessionMu.Lock()
	defer uploadSessionMu.Unlock()
	ctx, cancel := context.WithCancel(context.Background())
	sessionContexts.Set(sessionId, &types.SessionContext{
		Ctx:    ctx,
		Cancel: cancel,
	})
	return ctx
}

// GetSessionContext returns the context for the session, or nil if not found
func GetSessionContext(sessionId string) context.Context {
	uploadSessionMu.RLock()
	defer uploadSessionMu.RUnlock()
	sessCtx := sessionContexts.Get(sessionId)
	if sessCtx == nil {
		return nil
	}
	return sessCtx.Ctx
}

// IsSessionCancelled checks if the session has been cancelled
func IsSessionCancelled(sessionId string) bool {
	ctx := GetSessionContext(sessionId)
	if ctx == nil {
		return true // Session not found, treat as cancelled
	}
	select {
	case <-ctx.Done():
		return true
	default:
		return false
	}
}
