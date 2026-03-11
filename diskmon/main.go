package main

import (
	"fmt"
	"log"
	"os"
	"strconv"
	"time"
)

type Config struct {
	CheckPath  string
	Threshold  float64
	Interval   time.Duration
	QBUrl      string
	QBUsername string
	QBPassword string
	NtfyURL    string
}

func loadConfig() Config {
	cfg := Config{
		CheckPath: envOrDefault("DISKMON_CHECK_PATH", "/hostfs"),
		QBUrl:     envOrDefault("DISKMON_QB_URL", "http://gluetun:8080"),
		QBUsername: os.Getenv("DISKMON_QB_USERNAME"),
		QBPassword: os.Getenv("DISKMON_QB_PASSWORD"),
		NtfyURL:   os.Getenv("DISKMON_NTFY_URL"),
	}

	threshold, err := strconv.ParseFloat(envOrDefault("DISKMON_THRESHOLD", "85"), 64)
	if err != nil {
		log.Fatalf("Invalid DISKMON_THRESHOLD: %v", err)
	}
	cfg.Threshold = threshold

	interval, err := time.ParseDuration(envOrDefault("DISKMON_INTERVAL", "5m"))
	if err != nil {
		log.Fatalf("Invalid DISKMON_INTERVAL: %v", err)
	}
	cfg.Interval = interval

	return cfg
}

func envOrDefault(key, defaultVal string) string {
	if val := os.Getenv(key); val != "" {
		return val
	}
	return defaultVal
}

func main() {
	cfg := loadConfig()
	qb := NewQBClient(cfg.QBUrl, cfg.QBUsername, cfg.QBPassword)
	paused := false

	log.Printf("diskmon started: path=%s threshold=%.0f%% interval=%s", cfg.CheckPath, cfg.Threshold, cfg.Interval)

	// Run immediately on startup, then on ticker
	ticker := time.NewTicker(cfg.Interval)
	defer ticker.Stop()

	check := func() {
		usage, err := checkDiskUsage(cfg.CheckPath)
		if err != nil {
			log.Printf("Error checking disk: %v", err)
			return
		}

		log.Printf("Disk usage: %.1f%% (threshold: %.0f%%)", usage, cfg.Threshold)

		if usage >= cfg.Threshold && !paused {
			log.Println("Threshold exceeded, pausing all torrents")
			if err := qb.PauseAll(); err != nil {
				log.Printf("Error pausing torrents: %v", err)
				return
			}
			paused = true
			msg := fmt.Sprintf("Disk usage at %.1f%% — torrents paused", usage)
			sendNotification(cfg.NtfyURL, msg)
			log.Println(msg)
		} else if usage < cfg.Threshold && paused {
			log.Println("Usage below threshold, resuming all torrents")
			if err := qb.ResumeAll(); err != nil {
				log.Printf("Error resuming torrents: %v", err)
				return
			}
			paused = false
			msg := fmt.Sprintf("Disk usage at %.1f%% — torrents resumed", usage)
			sendNotification(cfg.NtfyURL, msg)
			log.Println(msg)
		}
	}

	check()
	for range ticker.C {
		check()
	}
}
