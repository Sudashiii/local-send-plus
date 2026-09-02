package boardcast

import (
	"fmt"
	"sync"
	"time"

	"github.com/moyoez/localsend-go/share"
	"github.com/moyoez/localsend-go/tool"
	"github.com/moyoez/localsend-go/types"
)

// SetScanConfig sets the current scan configuration for scan-now API
func SetScanConfig(mode types.ScanMode, selfMessage *types.VersionMessage, selfHTTP *types.VersionMessageHTTP, timeout int, httpTimeout int) {
	currentScanConfigMu.Lock()
	defer currentScanConfigMu.Unlock()
	currentScanConfig = &types.ScanConfig{
		Mode:        mode,
		SelfMessage: selfMessage,
		SelfHTTP:    selfHTTP,
		Timeout:     timeout,
		HTTPTimeout: httpTimeout,
	}
}

// GetScanConfig returns the current scan configuration
func GetScanConfig() *types.ScanConfig {
	currentScanConfigMu.RLock()
	defer currentScanConfigMu.RUnlock()
	return currentScanConfig
}

// ScanOnceUDP sends a single UDP multicast message to trigger device discovery.
func ScanOnceUDP(message *types.VersionMessage) error {
	return SendMulticastOnce(message)
}

// RestartAutoScan sends a restart signal to all running auto scan loops.
// skipHTTPImmediateScan: if true (e.g. after scan-now), HTTP loop only resets timeout; next scan in 30s.
func RestartAutoScan(skipHTTPImmediateScan bool) {
	autoScanControlMu.Lock()
	defer autoScanControlMu.Unlock()

	if autoScanRestartCh == nil {
		tool.DefaultLogger.Debug("No auto scan restart channel, creating one")
		autoScanRestartCh = make(chan restartAction, 1)
	}

	action := restartAction{SkipHTTPImmediateScan: skipHTTPImmediateScan}
	select {
	case autoScanRestartCh <- action:
		tool.DefaultLogger.Info("Auto scan restart signal sent")
	default:
		tool.DefaultLogger.Debug("Auto scan restart channel full, signal already pending")
	}
}

// scanNowRestartAutoScan restarts or resumes auto scan after scan-now completes.
func scanNowRestartAutoScan(config *types.ScanConfig) {
	if IsAutoScanRunning() {
		tool.DefaultLogger.Debug("Auto scan is running, sending restart signal (HTTP next scan in 30s)")
		RestartAutoScan(true)
	} else {
		tool.DefaultLogger.Info("Auto scan has stopped, restarting auto scan loops (HTTP first scan in 30s)")
		restartAutoScanLoops(config, true)
	}
}

// IsAutoScanRunning returns whether any auto scan loop is currently running.
func IsAutoScanRunning() bool {
	autoScanControlMu.Lock()
	defer autoScanControlMu.Unlock()
	return autoScanHTTPRunning || autoScanUDPRunning
}

// ScanNow performs scan-now: HTTP scan only (sync), then restarts/resumes normal auto scan in background.
// - scan-now: executes HTTP scan only; returns after HTTP scan completes so API can return device list.
// - other/normal scan: unchanged (auto scan by Mode: UDP, HTTP, or Mixed runs in background).
// When SelfHTTP is nil, falls back to legacy one-shot by Mode.
// Returns error if scan config is not set or scan fails.
func ScanNow() error {
	config := GetScanConfig()
	if config == nil {
		return fmt.Errorf("scan config not set")
	}

	tool.DefaultLogger.Info("Performing manual scan (HTTP)...")

	if config.SelfHTTP != nil {
		tool.DefaultLogger.Debug("scan-now: executing HTTP scan with default background scan options...")
		scanNowOpts := &HTTPScanOptions{Concurrency: autoScanConcurrencyLimit, RateLimitPPS: autoScanICMPRatePPS}

		// 1. First scan (wait for full completion)
		if err := ScanOnceHTTP(config.SelfHTTP, scanNowOpts); err != nil {
			return err
		}

		// 2. Check if devices found after first scan
		if len(share.ListUserScanCurrent()) > 0 {
			go scanNowRestartAutoScan(config)
			return nil
		}

		// 3. No devices found: start background retry loop (non-blocking)
		go scanNowBackgroundLoop(config, scanNowOpts)
		return nil
	}

	go func() {
		if IsAutoScanRunning() {
			tool.DefaultLogger.Debug("Auto scan is running, sending restart signal")
			RestartAutoScan(false)
		} else {
			tool.DefaultLogger.Info("Auto scan has stopped, restarting auto scan loops")
			restartAutoScanLoops(config, false)
		}
	}()

	switch config.Mode {
	case types.ScanModeUDP:
		if config.SelfMessage == nil {
			return fmt.Errorf("self message not configured for UDP scan")
		}
		tool.DefaultLogger.Debug("Sending UDP multicast scan...")
		return ScanOnceUDP(config.SelfMessage)

	case types.ScanModeHTTP:
		return fmt.Errorf("self HTTP message not configured for HTTP scan")

	case types.ScanModeMixed:
		var udpErr error
		var wg sync.WaitGroup
		if config.SelfMessage != nil {
			wg.Add(1)
			go func() {
				defer wg.Done()
				tool.DefaultLogger.Debug("Sending UDP multicast scan (mixed mode)...")
				udpErr = ScanOnceUDP(config.SelfMessage)
				if udpErr != nil {
					tool.DefaultLogger.Warnf("UDP scan failed: %v", udpErr)
				}
			}()
		}
		wg.Wait()
		if udpErr != nil {
			return udpErr
		}
		return nil

	default:
		return fmt.Errorf("unknown scan mode: %d", config.Mode)
	}
}

// scanNowBackgroundLoop runs in a background goroutine after scan-now returns empty.
// It retries HTTP scanning every 30s, up to HTTPTimeout (default 60s).
// Exits early if devices are found. On exit, restarts normal auto scan.
func scanNowBackgroundLoop(config *types.ScanConfig, opts *HTTPScanOptions) {
	httpTimeout := config.HTTPTimeout
	if httpTimeout <= 0 {
		httpTimeout = 60
	}
	tool.DefaultLogger.Infof("scan-now: no devices found, starting background retry loop (30s interval, %ds timeout)", httpTimeout)

	timeoutTimer := time.NewTimer(time.Duration(httpTimeout) * time.Second)
	defer timeoutTimer.Stop()

	ticker := time.NewTicker(30 * time.Second)
	defer ticker.Stop()

	for {
		select {
		case <-timeoutTimer.C:
			tool.DefaultLogger.Info("scan-now: background retry loop timed out")
			scanNowRestartAutoScan(config)
			return
		case <-ticker.C:
			if IsScanPaused() {
				tool.DefaultLogger.Debug("scan-now: background loop paused, skipping this tick")
				continue
			}
			if err := ScanOnceHTTP(config.SelfHTTP, opts); err != nil {
				tool.DefaultLogger.Warnf("scan-now: background HTTP scan failed: %v", err)
				continue
			}
			if len(share.ListUserScanCurrent()) > 0 {
				tool.DefaultLogger.Info("scan-now: background loop found devices, exiting")
				scanNowRestartAutoScan(config)
				return
			}
		}
	}
}

// restartAutoScanLoops restarts the auto scan goroutines based on configuration.
// skipHTTPInitialScan: if true (e.g. after scan-now), HTTP loop does not run initial scan; first scan in 30s.
func restartAutoScanLoops(config *types.ScanConfig, skipHTTPInitialScan bool) {
	if config == nil {
		return
	}
	udpTimeout := config.Timeout
	httpTimeout := config.HTTPTimeout
	if httpTimeout <= 0 {
		httpTimeout = config.Timeout
	}
	switch config.Mode {
	case types.ScanModeUDP:
		if config.SelfMessage != nil {
			go SendMulticastUsingUDPWithTimeout(config.SelfMessage, udpTimeout)
		}
	case types.ScanModeHTTP:
		if config.SelfHTTP != nil {
			go ListenMulticastUsingHTTPWithTimeout(config.SelfHTTP, httpTimeout, skipHTTPInitialScan)
		}
	case types.ScanModeMixed:
		if config.SelfMessage != nil {
			go SendMulticastUsingUDPWithTimeout(config.SelfMessage, udpTimeout)
		}
		if config.SelfHTTP != nil {
			go ListenMulticastUsingHTTPWithTimeout(config.SelfHTTP, httpTimeout, skipHTTPInitialScan)
		}
	}
}
