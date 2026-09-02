package models

import (
	"sync"
	"time"

	ttlworker "github.com/FloatTech/ttl"
	"github.com/moyoez/localsend-go/tool"
	"github.com/moyoez/localsend-go/types"
)

const (
	ShareSessionTTL = 3600 * time.Second // 1 hour
)

var (
	shareSessionMu        sync.RWMutex
	shareSessions         = ttlworker.NewCache[string, *types.ShareSession](ShareSessionTTL)
	confirmDownloadChans  = ttlworker.NewCache[string, chan types.ConfirmResult](tool.DefaultTTL)
	confirmedDownloadSess = ttlworker.NewCache[string, bool](ShareSessionTTL) // confirmed sessions.
)

// CacheShareSession stores a share session
func CacheShareSession(session *types.ShareSession) {
	shareSessionMu.Lock()
	defer shareSessionMu.Unlock()
	shareSessions.Set(session.SessionId, session)
}

// GetShareSession retrieves a share session by ID
func GetShareSession(sessionId string) (*types.ShareSession, bool) {
	shareSessionMu.RLock()
	defer shareSessionMu.RUnlock()
	sess := shareSessions.Get(sessionId)
	if sess == nil {
		return nil, false
	}
	return sess, true
}

// RemoveShareSession removes a share session
// confirmKey returns cache key for session+client (per-device confirm).
func confirmKey(sessionId, clientKey string) string {
	return sessionId + "\n" + clientKey
}

// RemoveShareSession removes a share session (confirm caches use per-client keys and will TTL out).
func RemoveShareSession(sessionId string) {
	shareSessionMu.Lock()
	defer shareSessionMu.Unlock()
	shareSessions.Delete(sessionId)
}

// IsDownloadConfirmed returns true if this client has been confirmed for this session (per-device).
func IsDownloadConfirmed(sessionId, clientKey string) bool {
	shareSessionMu.RLock()
	defer shareSessionMu.RUnlock()
	return confirmedDownloadSess.Get(confirmKey(sessionId, clientKey))
}

// MarkDownloadConfirmed marks this client as confirmed for the session after user agrees.
func MarkDownloadConfirmed(sessionId, clientKey string) {
	shareSessionMu.Lock()
	defer shareSessionMu.Unlock()
	confirmedDownloadSess.Set(confirmKey(sessionId, clientKey), true)
}

// SetConfirmDownloadChannel sets the channel for confirm-download callback (per clientKey).
func SetConfirmDownloadChannel(sessionId, clientKey string, ch chan types.ConfirmResult) {
	shareSessionMu.Lock()
	defer shareSessionMu.Unlock()
	confirmDownloadChans.Set(confirmKey(sessionId, clientKey), ch)
}

// GetConfirmDownloadChannel retrieves the confirm-download channel for this client.
func GetConfirmDownloadChannel(sessionId, clientKey string) (chan types.ConfirmResult, bool) {
	shareSessionMu.RLock()
	defer shareSessionMu.RUnlock()
	ch := confirmDownloadChans.Get(confirmKey(sessionId, clientKey))
	if ch == nil {
		return nil, false
	}
	return ch, true
}

// DeleteConfirmDownloadChannel removes the confirm-download channel for this client.
func DeleteConfirmDownloadChannel(sessionId, clientKey string) {
	shareSessionMu.Lock()
	defer shareSessionMu.Unlock()
	confirmDownloadChans.Delete(confirmKey(sessionId, clientKey))
}

// GetShareSessionFiles returns the files map for prepare-download response
func GetShareSessionFiles(session *types.ShareSession) map[string]types.FileInfo {
	files := make(map[string]types.FileInfo, len(session.Files))
	for id, entry := range session.Files {
		files[id] = entry.FileInfo
	}
	return files
}

// LookupShareFile looks up a file in a share session
func LookupShareFile(session *types.ShareSession, fileId string) (types.ShareFileEntry, bool) {
	entry, ok := session.Files[fileId]
	return entry, ok
}
